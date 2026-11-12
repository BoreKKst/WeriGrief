"""
Google Sheets Monitor for WeriGrief Staff Bot.
Monitors:
1. "Учет предупреждений" - addition, removal, and monthly reset of warnings.
2. "Списки" - staff appointments, promotions, and removals ("админ пока").
"""

import json
import os
import re
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

log = logging.getLogger("werigrief_staff.sheets")

# Full read/write scope for spreadsheets and Drive read scope for modifier detection
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]
WARNINGS_SHEET = "Учет предупреждений"
STAFF_SHEET = "Списки"

# Known staff emails mapping
STAFF_EMAILS = {
    "borekkst": "nursatmagsat@gmail.com",
}


def resolve_email_to_nick(val: str) -> str:
    """If val matches an email in STAFF_EMAILS, return the corresponding nickname."""
    if not val:
        return val
    cleaned = val.strip().lower()
    for nick, email in STAFF_EMAILS.items():
        if email.lower() == cleaned:
            return nick
    return val


def normalize_date_str(val: any) -> str:
    """
    Normalizes any date representation (Excel/Sheets serial number like 46297,
    ISO format YYYY-MM-DD, D.M.Y, etc.) into standard 'DD.MM.YYYY'.
    """
    if val is None:
        return ""
    s = str(val).strip()
    if not s:
        return ""

    # Check if it's an Excel / Google Sheets serial number (e.g. 46297 or 46297.0)
    try:
        f_val = float(s)
        # Serial dates around 2020-2040 are roughly between 43800 and 51000
        if 30000 <= f_val <= 60000:
            d = datetime(1899, 12, 30) + timedelta(days=f_val)
            return d.strftime("%d.%m.%Y")
    except ValueError:
        pass

    # Check for DD.MM.YYYY or D.M.YYYY
    m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})$", s)
    if m:
        d, mth, y = m.groups()
        if len(y) == 2:
            y = "20" + y
        return f"{int(d):02d}.{int(mth):02d}.{y}"

    # Check for YYYY-MM-DD
    m = re.match(r"^(\d{4})[./-](\d{1,2})[./-](\d{1,2})$", s)
    if m:
        y, mth, d = m.groups()
        return f"{int(d):02d}.{int(mth):02d}.{y}"

    return s


def _is_complete_warn_row(row: list) -> bool:
    """A warning row is complete when all 4 columns are filled:
    [0] Игровой никнейм, [1] Дата выдачи, [2] Причина, [3] Кто выдал
    """
    if len(row) < 4:
        return False
    return all(bool(str(cell).strip()) for cell in row[:4])


class SheetsMonitor:
    def __init__(self, creds_file: str, spreadsheet_id: str):
        self.spreadsheet_id = spreadsheet_id
        self.known_warnings: list[str] = []  # list of "nick|date|reason|issuer"
        self.known_staff: dict[str, dict] = {}  # nick.lower() -> staff dict
        self.dismissed_history: list[dict] = []  # audit ledger of dismissed staff
        self.last_reset_month: str = ""  # YYYY-MM of the last monthly reset
        self.last_playtime_date: str = ""  # YYYY-MM-DD of the last daily playtime check
        self.custom_exempt_staff: list[str] = []  # nicks exempt from norm by admin command
        self.mc_playtime_snapshots: dict[str, dict] = {}  # { nick_low: { "hours": float, "date": str, "timestamp": float } }
        self._state_file = Path(__file__).resolve().parent / "sheets_state.json"

        # Authenticate with read/write credentials
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        self.service = build("sheets", "v4", credentials=creds)
        self.sheets = self.service.spreadsheets()
        self.drive = None
        try:
            self.drive = build("drive", "v3", credentials=creds)
        except Exception as e:
            log.warning(f"Could not initialize Drive API client: {e}")

        # Load persisted state
        self._load_state()

        # If last_reset_month is unset, set to current month so we don't clear on first boot
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        if not self.last_reset_month:
            self.last_reset_month = current_month

        # Initial sync for warnings
        if not self.known_warnings:
            try:
                rows = self._fetch_warnings()
                self.known_warnings = [self._row_key(r) for r in rows if _is_complete_warn_row(r)]
                log.info(f"Initial sync: {len(self.known_warnings)} complete warnings recorded")
            except Exception as e:
                log.error(f"Initial warnings sync failed: {e}")

        # Initial sync for staff
        if not self.known_staff:
            try:
                staff_list = self.get_staff_list()
                for s in staff_list:
                    if s.get("nick"):
                        self.known_staff[s["nick"].lower()] = s
                log.info(f"Initial sync: {len(self.known_staff)} staff members recorded")
            except Exception as e:
                log.error(f"Initial staff sync failed: {e}")

        self._save_state()

    def _row_key(self, row: list) -> str:
        """Create a unique key for a warning row."""
        nick = str(row[0]).strip().lower() if len(row) > 0 else ""
        date = normalize_date_str(row[1]) if len(row) > 1 else ""
        reason = str(row[2]).strip() if len(row) > 2 else ""
        issued = str(row[3]).strip().lower() if len(row) > 3 else ""
        return f"{nick}|{date}|{reason}|{issued}"

    def _load_state(self):
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text("utf-8"))
                if "known_warnings" in data:
                    cleaned_known = []
                    for kw in data.get("known_warnings", []):
                        parts = kw.split("|")
                        if len(parts) >= 4:
                            cleaned_known.append(f"{parts[0]}|{normalize_date_str(parts[1])}|{parts[2]}|{parts[3]}")
                        else:
                            cleaned_known.append(kw)
                    self.known_warnings = cleaned_known
                if "known_staff" in data:
                    self.known_staff = data.get("known_staff", {})
                if "last_reset_month" in data:
                    self.last_reset_month = data.get("last_reset_month", "")
                if "last_playtime_date" in data:
                    self.last_playtime_date = data.get("last_playtime_date", "")
                if "dismissed_history" in data:
                    self.dismissed_history = data.get("dismissed_history", [])
                if "custom_exempt_staff" in data:
                    self.custom_exempt_staff = data.get("custom_exempt_staff", [])
                if "mc_playtime_snapshots" in data:
                    self.mc_playtime_snapshots = data.get("mc_playtime_snapshots", {})
                log.info(f"Loaded state: {len(self.known_warnings)} warnings, {len(self.known_staff)} staff, {len(self.dismissed_history)} dismissed records, month: {self.last_reset_month}, exempt: {len(self.custom_exempt_staff)}")
            except Exception as e:
                log.warning(f"Failed to load state: {e}")
                self.known_warnings = []
                self.known_staff = {}
                self.dismissed_history = []
                self.last_reset_month = ""
                self.last_playtime_date = ""
                self.custom_exempt_staff = []
                self.mc_playtime_snapshots = {}

    def _save_state(self):
        try:
            self._state_file.write_text(
                json.dumps(
                    {
                        "known_warnings": self.known_warnings,
                        "known_staff": self.known_staff,
                        "dismissed_history": self.dismissed_history,
                        "last_reset_month": self.last_reset_month,
                        "last_playtime_date": self.last_playtime_date,
                        "custom_exempt_staff": self.custom_exempt_staff,
                        "mc_playtime_snapshots": self.mc_playtime_snapshots,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            log.error(f"Failed to save state: {e}")

    def _fetch_warnings(self) -> list[list[str]]:
        """Fetch all rows from 'Учет предупреждений' sheet, replacing any email with nickname."""
        result = (
            self.sheets.values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{WARNINGS_SHEET}'!A:D",
                valueRenderOption="FORMATTED_VALUE",
            )
            .execute()
        )
        values = result.get("values", [])
        if values and values[0] and str(values[0][0]).strip() in ("Игровой никнейм", "Никнейм"):
            values = values[1:]

        cleaned = []
        for r in values:
            if not r:
                continue
            r_copy = list(r)
            if len(r_copy) > 0 and r_copy[0]:
                r_copy[0] = resolve_email_to_nick(str(r_copy[0]).strip())
            if len(r_copy) > 1 and r_copy[1]:
                r_copy[1] = normalize_date_str(r_copy[1])
            if len(r_copy) > 3 and r_copy[3]:
                r_copy[3] = resolve_email_to_nick(str(r_copy[3]).strip())
            cleaned.append(r_copy)
        return cleaned

    def check_warnings(self) -> dict:
        """
        Check for both added and removed warnings.
        Groups removals by player for clear announcements.
        Returns:
            {
                "new": [ {nick, date, reason, issued_by, total_warns} ],
                "removed_by_player": {
                    "player_nick": {
                        "removed_count": 2,
                        "current_count": 1,
                        "removed_items": [ {date, reason, issued_by}, ... ]
                    }
                },
                "current_counts": { nick_low: count }
            }
        """
        rows = self._fetch_warnings()
        complete_rows = [r for r in rows if _is_complete_warn_row(r)]

        current_keys = {self._row_key(r) for r in complete_rows}
        known_keys = set(self.known_warnings)

        new_keys = current_keys - known_keys
        removed_keys = known_keys - current_keys

        # Compute total warnings per player right now
        warn_counts: dict[str, int] = {}
        for row in complete_rows:
            nick = str(row[0]).strip()
            warn_counts[nick.lower()] = warn_counts.get(nick.lower(), 0) + 1

        new_results = []
        if new_keys:
            for row in complete_rows:
                key = self._row_key(row)
                if key in new_keys:
                    nick = str(row[0]).strip()
                    new_results.append({
                        "nick": nick,
                        "date": normalize_date_str(row[1]) if len(row) > 1 else "",
                        "reason": str(row[2]).strip(),
                        "issued_by": str(row[3]).strip(),
                        "total_warns": warn_counts.get(nick.lower(), 1),
                    })

        # Group removed warnings by player
        removed_by_player: dict[str, dict] = {}
        if removed_keys:
            for key in removed_keys:
                parts = key.split("|")
                r_nick = parts[0] if len(parts) > 0 else ""
                r_date = normalize_date_str(parts[1]) if len(parts) > 1 else ""
                r_reason = parts[2] if len(parts) > 2 else ""
                r_issuer = parts[3] if len(parts) > 3 else ""

                if r_nick not in removed_by_player:
                    removed_by_player[r_nick] = {
                        "nick": r_nick,
                        "removed_items": [],
                        "current_count": warn_counts.get(r_nick.lower(), 0),
                    }
                removed_by_player[r_nick]["removed_items"].append({
                    "date": r_date,
                    "reason": r_reason,
                    "issued_by": r_issuer,
                })

            for pinfo in removed_by_player.values():
                pinfo["removed_count"] = len(pinfo["removed_items"])

        if new_keys or removed_keys:
            self.known_warnings = list(current_keys)
            self._save_state()

        return {
            "new": new_results,
            "removed_by_player": removed_by_player,
            "current_counts": warn_counts,
        }

    def get_last_modifier(self) -> str:
        """
        Attempts to determine who modified the sheet via Google Drive API.
        Resolves their email to their known nickname, never leaking the email.
        """
        if not self.drive:
            return ""
        try:
            f = self.drive.files().get(
                fileId=self.spreadsheet_id,
                fields="lastModifyingUser(displayName,emailAddress)"
            ).execute()
            user = f.get("lastModifyingUser") or {}
            email = (user.get("emailAddress") or "").strip().lower()
            name = (user.get("displayName") or "").strip()

            if email:
                nick = resolve_email_to_nick(email)
                if nick.lower() != email:
                    return nick

            if name:
                name_low = name.lower()
                for s_nick in STAFF_EMAILS:
                    if s_nick in name_low:
                        return s_nick
                return name
        except Exception as e:
            log.debug(f"Could not retrieve last modifier from Drive API: {e}")
        return ""

    def check_new_warnings(self) -> list[dict]:
        res = self.check_warnings()
        return res["new"]

    def check_staff_changes(self) -> dict:
        """
        Check for staff appointments, promotions, or removals ("админ пока").
        """
        current_staff_list = self.get_staff_list()
        current_map: dict[str, dict] = {}
        for s in current_staff_list:
            if s.get("nick"):
                current_map[s["nick"].lower()] = s

        appointed = []
        promoted = []
        removed = []

        # Detect appointments & promotions
        for nick_low, curr in current_map.items():
            if nick_low not in self.known_staff:
                curr_copy = dict(curr)
                curr_copy["email"] = STAFF_EMAILS.get(nick_low, "")
                appointer = self.get_last_modifier()
                curr_copy["appointed_by"] = appointer or "Руководство проекта"
                appointed.append(curr_copy)
            else:
                old = self.known_staff[nick_low]
                old_pos = old.get("position", "").strip()
                new_pos = curr.get("position", "").strip()
                if old_pos and new_pos and old_pos.lower() != new_pos.lower():
                    promoter = self.get_last_modifier()
                    promoted.append({
                        "nick": curr["nick"],
                        "old_position": old_pos,
                        "new_position": new_pos,
                        "discord": curr.get("discord", ""),
                        "email": STAFF_EMAILS.get(nick_low, ""),
                        "promoted_by": promoter or "Руководство проекта",
                    })

        # Detect removals ("админ пока")
        for nick_low, old in self.known_staff.items():
            if nick_low not in current_map:
                old_copy = dict(old)
                old_copy["email"] = STAFF_EMAILS.get(nick_low, "")

                # Automatically clear all warnings for dismissed staff from 'Учет предупреждений'!
                warns_cleared = self.clear_player_warnings(old_copy["nick"])
                old_copy["warns_cleared"] = warns_cleared

                # Identify who made the change
                remover = self.get_last_modifier()
                old_copy["removed_by"] = remover or "Руководство проекта"

                # Record in dismissed staff audit ledger
                dismiss_entry = {
                    "nick": old_copy["nick"],
                    "position": old_copy.get("position", "—"),
                    "discord": old_copy.get("discord", "—"),
                    "email": old_copy.get("email", ""),
                    "removed_by": old_copy["removed_by"],
                    "dismissed_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
                    "warns_cleared": warns_cleared,
                }
                self.dismissed_history.append(dismiss_entry)
                log.info(f"Staff member {old_copy['nick']} dismissed by {old_copy['removed_by']}: automatically cleared {warns_cleared} warnings from sheets")
                removed.append(old_copy)

        if appointed or promoted or removed:
            self.known_staff = current_map
            self._save_state()

        return {
            "appointed": appointed,
            "promoted": promoted,
            "removed": removed,
        }

    def remove_staff_member(self, target_nick: str, removed_by: str = "", reason: str = "") -> dict | None:
        """
        Removes a staff member from 'Списки' sheet, clears their warnings from
        'Учет предупреждений', records in audit history, and returns removed info.
        """
        try:
            target_low = target_nick.strip().lower()
            current_staff = self.get_staff_list()

            target_entry = None
            for s in current_staff:
                if s.get("nick") and s["nick"].lower() == target_low:
                    target_entry = s
                    break

            if not target_entry:
                return None

            actual_nick = target_entry["nick"]

            # Read all rows of 'Списки' to find row index
            res = (
                self.sheets.values()
                .get(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{STAFF_SHEET}'!A1:A50",
                )
                .execute()
            )
            rows = res.get("values", [])
            row_idx = None
            for idx, r in enumerate(rows, 1):
                if r and resolve_email_to_nick(str(r[0]).strip()).lower() == target_low:
                    row_idx = idx
                    break

            if row_idx is not None:
                body = {
                    "requests": [
                        {
                            "deleteDimension": {
                                "range": {
                                    "sheetId": 0,
                                    "dimension": "ROWS",
                                    "startIndex": row_idx - 1,
                                    "endIndex": row_idx,
                                }
                            }
                        }
                    ]
                }
                self.sheets.batchUpdate(spreadsheetId=self.spreadsheet_id, body=body).execute()
                log.info(f"Deleted row {row_idx} ({actual_nick}) from '{STAFF_SHEET}'")

            warns_cleared = self.clear_player_warnings(actual_nick)

            dismiss_entry = {
                "nick": actual_nick,
                "position": target_entry.get("position", "—"),
                "discord": target_entry.get("discord", "—"),
                "email": target_entry.get("email", ""),
                "removed_by": resolve_email_to_nick(removed_by) if removed_by else "Руководство проекта",
                "reason": reason,
                "dismissed_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
                "warns_cleared": warns_cleared,
            }
            self.dismissed_history.append(dismiss_entry)

            if target_low in self.known_staff:
                del self.known_staff[target_low]
            self._save_state()

            return dismiss_entry
        except Exception as e:
            log.error(f"Error removing staff member {target_nick}: {e}")
            return None

    def add_staff_member(
        self,
        nick: str,
        position: str,
        discord_name: str = "",
        telegram: str = "",
        comment: str = "",
        appointed_by: str = "",
    ) -> dict | None:
        """
        Appends a new staff member to 'Списки' sheet and updates known_staff.
        Returns the staff member dict if successful, None otherwise.
        """
        try:
            nick_clean = nick.strip()
            target_low = nick_clean.lower()
            current_staff = self.get_staff_list()

            for s in current_staff:
                if s.get("nick") and s["nick"].lower() == target_low:
                    log.warning(f"Staff member {nick_clean} already exists in '{STAFF_SHEET}'")
                    return s

            row_data = [
                nick_clean,
                position.strip(),
                discord_name.strip(),
                telegram.strip(),
                comment.strip(),
                "0",  # invited
                "0",  # hours
                "0",  # warns
            ]

            body = {"values": [row_data]}
            self.sheets.values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{STAFF_SHEET}'!A:H",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body,
            ).execute()

            entry = {
                "nick": nick_clean,
                "position": position.strip(),
                "discord": discord_name.strip(),
                "telegram": telegram.strip(),
                "comment": comment.strip(),
                "invited": "0",
                "hours": "0",
                "warns": "0",
                "email": STAFF_EMAILS.get(target_low, ""),
                "appointed_by": resolve_email_to_nick(appointed_by) if appointed_by else "Руководство проекта",
            }
            self.known_staff[target_low] = entry
            self._save_state()
            log.info(f"Added new staff member to '{STAFF_SHEET}': {nick_clean} ({position})")
            return entry
        except Exception as e:
            log.error(f"Failed to add staff member {nick}: {e}")
            return None

    def clean_former_staff_warnings(self) -> dict:
        """
        Scans 'Учет предупреждений' and purges any warnings belonging to
        nicknames that are NOT in active staff list 'Списки'.
        Returns dict of { former_nick: count_removed }.
        """
        try:
            current_staff = self.get_staff_list()
            active_nicks = {s["nick"].lower() for s in current_staff if s.get("nick")}

            rows = self._fetch_warnings()
            kept = []
            deleted_summary = {}

            for r in rows:
                if not r or not str(r[0]).strip():
                    continue
                nick = str(r[0]).strip()
                if nick.lower() in active_nicks and _is_complete_warn_row(r):
                    kept.append(r[:4])
                else:
                    deleted_summary[nick] = deleted_summary.get(nick, 0) + 1

            if deleted_summary:
                self.sheets.values().clear(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{WARNINGS_SHEET}'!A2:D500",
                ).execute()

                if kept:
                    self.sheets.values().update(
                        spreadsheetId=self.spreadsheet_id,
                        range=f"'{WARNINGS_SHEET}'!A2",
                        valueInputOption="USER_ENTERED",
                        body={"values": kept},
                    ).execute()

                self.known_warnings = [self._row_key(r) for r in kept if _is_complete_warn_row(r)]
                self._save_state()
                log.info(f"Purged former staff warnings: {deleted_summary}")

            return deleted_summary
        except Exception as e:
            log.error(f"Error purging former staff warnings: {e}")
            return {}

    def reset_monthly_warnings(self) -> bool:
        """
        Clears all warning records (rows A2:D) in 'Учет предупреждений',
        keeping the column header titles in row 1 intact.
        Also resets 'Предупреждения' column in 'Списки' to 0 for all staff.
        """
        try:
            log.info("Executing monthly warning reset on Google Sheets...")

            # 1. Clear warning data rows in 'Учет предупреждений' (preserve row 1 header)
            self.sheets.values().clear(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{WARNINGS_SHEET}'!A2:D500",
            ).execute()

            # Note: Column H in 'Списки' has the formula =COUNTIF('Учет предупреждений'!$A:$A; A...),
            # so Google Sheets automatically recalculates counts to 0 without touching the formulas!

            # 2. Update state
            self.known_warnings = []
            self.last_reset_month = datetime.now(timezone.utc).strftime("%Y-%m")
            self._save_state()

            log.info("Monthly warnings reset completed successfully!")
            return True
        except Exception as e:
            log.error(f"Failed to reset monthly warnings: {e}")
            return False

    def clear_player_warnings(self, target_nick: str) -> int:
        """
        Removes all warning rows for a specific player from 'Учет предупреждений'
        and updates their warn count in 'Списки'.
        Returns number of warnings removed.
        """
        try:
            target_low = target_nick.strip().lower()

            # Read all rows including header
            res = (
                self.sheets.values()
                .get(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{WARNINGS_SHEET}'!A1:D500",
                )
                .execute()
            )
            values = res.get("values", [])
            if not values:
                return 0

            header = values[0]
            data_rows = values[1:]

            target_no_us = target_low.replace("_", "")
            kept_rows = []
            removed_count = 0
            for r in data_rows:
                if not r or len(r) == 0 or not _is_complete_warn_row(r):
                    continue
                r_nick = str(r[0]).strip().lower()
                if r_nick == target_low or r_nick.replace("_", "") == target_no_us:
                    removed_count += 1
                else:
                    kept_rows.append([
                        str(r[0]).strip(),
                        normalize_date_str(r[1]) if len(r) > 1 else "",
                        str(r[2]).strip() if len(r) > 2 else "",
                        str(r[3]).strip() if len(r) > 3 else "",
                    ])

            if removed_count == 0:
                return 0

            # Clear existing data rows
            self.sheets.values().clear(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{WARNINGS_SHEET}'!A2:D500",
            ).execute()

            # Write back kept rows if any
            if kept_rows:
                self.sheets.values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{WARNINGS_SHEET}'!A2",
                    valueInputOption="USER_ENTERED",
                    body={"values": kept_rows},
                ).execute()

            # Resync local state
            self.known_warnings = [self._row_key(r) for r in kept_rows if _is_complete_warn_row(r)]
            self._save_state()

            log.info(f"Cleared {removed_count} warning(s) for player {target_nick}")
            return removed_count
        except Exception as e:
            log.error(f"Error clearing warnings for {target_nick}: {e}")
            return 0

    def remove_warning(
        self,
        target_nick: str,
        case_index: int | None = None,
        reason_keyword: str | None = None,
    ) -> tuple[bool, dict | None, int]:
        """
        Removes a specific warning row for target_nick from 'Учет предупреждений'.
        case_index: 1-based index (e.g. 1, 2) matching `/warns` display.
        reason_keyword: optional substring in reason to match.
        If neither is provided, removes the latest (last) warning.
        Returns (success, removed_warning_dict, remaining_total_count).
        """
        try:
            target_low = target_nick.strip().lower()
            target_no_us = target_low.replace("_", "")

            res = (
                self.sheets.values()
                .get(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{WARNINGS_SHEET}'!A1:D500",
                )
                .execute()
            )
            values = res.get("values", [])
            if not values or len(values) < 2:
                return False, None, 0

            header = values[0]
            data_rows = values[1:]

            # Find matching warning rows for player
            matched_indices = []
            for idx, r in enumerate(data_rows):
                if r and len(r) > 0 and _is_complete_warn_row(r):
                    r_nick = str(r[0]).strip().lower()
                    if r_nick == target_low or r_nick.replace("_", "") == target_no_us:
                        matched_indices.append((idx, r))

            if not matched_indices:
                return False, None, 0

            target_match = None
            if case_index is not None and 1 <= case_index <= len(matched_indices):
                target_match = matched_indices[case_index - 1]
            elif reason_keyword:
                kw = reason_keyword.strip().lower()
                for item in reversed(matched_indices):
                    r = item[1]
                    reason = str(r[2]).strip().lower() if len(r) > 2 else ""
                    if kw in reason:
                        target_match = item
                        break

            if not target_match:
                # Default to last warning
                target_match = matched_indices[-1]

            row_to_remove_idx, removed_row = target_match
            case_num = matched_indices.index(target_match) + 1
            removed_info = {
                "nick": str(removed_row[0]).strip(),
                "date": normalize_date_str(removed_row[1]) if len(removed_row) > 1 else "",
                "reason": str(removed_row[2]).strip() if len(removed_row) > 2 else "",
                "issued_by": str(removed_row[3]).strip() if len(removed_row) > 3 else "",
                "case_num": case_num,
            }

            kept_rows = [
                [
                    str(r[0]).strip(),
                    normalize_date_str(r[1]) if len(r) > 1 else "",
                    str(r[2]).strip() if len(r) > 2 else "",
                    str(r[3]).strip() if len(r) > 3 else "",
                ]
                for idx, r in enumerate(data_rows)
                if idx != row_to_remove_idx and r and len(r) > 0 and _is_complete_warn_row(r)
            ]

            # Clear existing data rows
            self.sheets.values().clear(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{WARNINGS_SHEET}'!A2:D500",
            ).execute()

            # Write back kept rows if any
            if kept_rows:
                self.sheets.values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{WARNINGS_SHEET}'!A2",
                    valueInputOption="USER_ENTERED",
                    body={"values": kept_rows},
                ).execute()

            # Resync local state
            self.known_warnings = [self._row_key(r) for r in kept_rows if _is_complete_warn_row(r)]
            self._save_state()

            # Remaining count for this player
            remaining_count = sum(
                1 for r in kept_rows
                if r and len(r) > 0 and _is_complete_warn_row(r) and (
                    str(r[0]).strip().lower() == target_low
                    or str(r[0]).strip().lower().replace("_", "") == target_no_us
                )
            )

            log.info(f"Removed warning #{case_num} for {target_nick}: {removed_info['reason']} (remaining: {remaining_count})")
            return True, removed_info, remaining_count
        except Exception as e:
            log.error(f"Error removing warning for {target_nick}: {e}")
            return False, None, 0

    def increment_staff_hours(self, deltas: dict[str, float]) -> dict[str, float]:
        """
        deltas: { nick_low: delta_hours_float }
        Reads current hours in Column G of 'Списки', adds delta_hours,
        and updates Column G with the new sum.
        Returns { nick_low: new_hours_float }
        """
        try:
            res = (
                self.sheets.values()
                .get(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{STAFF_SHEET}'!A1:G50",
                )
                .execute()
            )
            rows = res.get("values", [])
            data = []
            result_map = {}
            for idx, r in enumerate(rows, 1):
                if r and str(r[0]).strip():
                    nick = resolve_email_to_nick(str(r[0]).strip()).lower()
                    if nick in deltas:
                        delta = deltas[nick]
                        curr_str = str(r[6]).strip() if len(r) > 6 and r[6] else "0"
                        try:
                            curr_val = float(curr_str.replace(",", "."))
                        except ValueError:
                            curr_val = 0.0
                        new_val = max(0.0, curr_val + delta)
                        result_map[nick] = new_val
                        # Format as int if whole number, else 1 decimal place
                        val_str = str(int(round(new_val))) if abs(new_val - round(new_val)) < 0.05 else f"{new_val:.1f}"
                        data.append({
                            "range": f"'{STAFF_SHEET}'!G{idx}",
                            "values": [[val_str]]
                        })
            if data:
                self.sheets.values().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"valueInputOption": "USER_ENTERED", "data": data}
                ).execute()
                log.info(f"Incremented staff hours in '{STAFF_SHEET}' for {len(data)} staff members: {result_map}")
            return result_map
        except Exception as e:
            log.error(f"Failed to increment staff hours in sheets: {e}")
            return {}

    def set_norm_exempt(self, nick: str, is_exempt: bool) -> bool:
        """Adds or removes nick from custom_exempt_staff with canonical resolution."""
        nick_low = nick.strip().lower()
        canonical_nick = nick_low
        try:
            for s in self.get_staff_list():
                s_nick = (s.get("nick") or "").strip().lower()
                if s_nick == nick_low or s_nick.replace("_", "") == nick_low.replace("_", ""):
                    canonical_nick = s_nick
                    break
        except Exception:
            pass

        changed = False
        target_no_us = canonical_nick.replace("_", "")
        if is_exempt:
            already = any(e.lower() == canonical_nick or e.lower().replace("_", "") == target_no_us for e in self.custom_exempt_staff)
            if not already:
                self.custom_exempt_staff.append(canonical_nick)
                changed = True
        else:
            to_remove = [e for e in self.custom_exempt_staff if e.lower() == canonical_nick or e.lower().replace("_", "") == target_no_us]
            if to_remove:
                for r in to_remove:
                    self.custom_exempt_staff.remove(r)
                changed = True
        if changed:
            self._save_state()
            log.info(f"Updated custom norm exemption for {canonical_nick}: exempt={is_exempt}")
        return changed

    def is_custom_exempt(self, nick: str) -> bool:
        if not nick:
            return False
        target = nick.strip().lower()
        target_no_us = target.replace("_", "")
        for ex in self.custom_exempt_staff:
            ex_low = ex.strip().lower()
            if target == ex_low or target_no_us == ex_low.replace("_", ""):
                return True
        return False

    def update_multiple_staff_hours(self, hours_map: dict[str, str]) -> int:
        """
        hours_map: { nick_low: hours_str }
        Updates Column G ('Кол-во отыгранных часов') in 'Списки' for all matching staff.
        """
        try:
            res = (
                self.sheets.values()
                .get(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{STAFF_SHEET}'!A1:A50",
                )
                .execute()
            )
            rows = res.get("values", [])
            data = []
            updated_count = 0
            for idx, r in enumerate(rows, 1):
                if r and str(r[0]).strip():
                    nick = resolve_email_to_nick(str(r[0]).strip()).lower()
                    if nick in hours_map:
                        data.append({
                            "range": f"'{STAFF_SHEET}'!G{idx}",
                            "values": [[hours_map[nick]]]
                        })
                        updated_count += 1
            if data:
                self.sheets.values().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"valueInputOption": "USER_ENTERED", "data": data}
                ).execute()
                log.info(f"Updated playtime hours for {updated_count} staff members in '{STAFF_SHEET}'")
            return updated_count
        except Exception as e:
            log.error(f"Failed to update staff hours in sheets: {e}")
            return 0

    def add_warning(self, nick: str, date: str, reason: str, issuer: str) -> int:
        """
        Appends a new warning row to 'Учет предупреждений'
        and increments the warning count in 'Списки'.
        Returns the new total count of warnings for this player.
        """
        try:
            norm_date = normalize_date_str(date)
            row = [nick, norm_date, reason, issuer]
            append_res = self.sheets.values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{WARNINGS_SHEET}'!A:D",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            ).execute()

            # Ensure the newly added row has clean formatting (no inherited header background)
            try:
                updated_range = append_res.get("updates", {}).get("updatedRange", "")
                m = re.search(r"!A(\d+):[A-Z](\d+)", updated_range)
                if m:
                    r_start = int(m.group(1)) - 1
                    r_end = int(m.group(2))
                    clean_req = {
                        "repeatCell": {
                            "range": {
                                "sheetId": 2010059507,
                                "startRowIndex": r_start,
                                "endRowIndex": r_end,
                                "startColumnIndex": 0,
                                "endColumnIndex": 4,
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "textFormat": {
                                        "foregroundColor": {"red": 0.1, "green": 0.1, "blue": 0.1},
                                        "bold": False,
                                    },
                                }
                            },
                            "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.foregroundColor,userEnteredFormat.textFormat.bold",
                        }
                    }
                    self.sheets.batchUpdate(
                        spreadsheetId=self.spreadsheet_id,
                        body={"requests": [clean_req]},
                    ).execute()
            except Exception as fe:
                log.debug(f"Could not clear row format for added warning: {fe}")

            # Record in known_warnings so poll loop doesn't double-process
            self.known_warnings.append(self._row_key(row))
            self._save_state()

            # Calculate total warns for this player
            rows = self._fetch_warnings()
            total_player_warns = 0
            for r in rows:
                if _is_complete_warn_row(r) and str(r[0]).strip().lower() == nick.lower():
                    total_player_warns += 1

            log.info(f"Added warning for {nick}: {reason} (by {issuer}, total: {total_player_warns})")
            return total_player_warns
        except Exception as e:
            log.error(f"Error adding warning for {nick}: {e}")
            return 1

    def get_warnings_for(self, query: str) -> dict:
        """
        Get all warnings for a query (matches MC nick, Discord, email, or partial).
        """
        q = query.strip().lower()

        # Find matching staff entry
        staff_list = self.get_staff_list()
        matched_staff = None

        # 1. Exact match on MC nick
        for s in staff_list:
            if s.get("nick") and s["nick"].lower() == q:
                matched_staff = s
                break

        # 2. Match on Discord
        if not matched_staff:
            for s in staff_list:
                d = s.get("discord", "").strip().lower()
                if d and (d == q or d.replace("_", "") == q.replace("_", "")):
                    matched_staff = s
                    break

        # 3. Match on Email
        if not matched_staff:
            for nick_k, email in STAFF_EMAILS.items():
                if email.lower() == q or q in email.lower():
                    for s in staff_list:
                        if s.get("nick") and s["nick"].lower() == nick_k:
                            matched_staff = s
                            break
                    break

        target_nick = matched_staff["nick"] if matched_staff else query

        # Search warnings table
        rows = self._fetch_warnings()
        results = []
        for row in rows:
            if _is_complete_warn_row(row):
                row_nick = str(row[0]).strip().lower()
                match = (
                    row_nick == target_nick.lower()
                    or row_nick == q
                    or row_nick.replace("_", "") == q.replace("_", "")
                )
                if match:
                    results.append({
                        "date": normalize_date_str(row[1]) if len(row) > 1 else "",
                        "reason": str(row[2]).strip(),
                        "issued_by": str(row[3]).strip(),
                    })

        sheet_warn_count = matched_staff.get("warns", len(results)) if matched_staff else len(results)

        return {
            "nick": target_nick,
            "warnings": results,
            "sheet_warn_count": sheet_warn_count,
            "staff_info": matched_staff,
        }

    def get_staff_list(self) -> list[dict]:
        """Fetch current staff list from 'Списки' sheet."""
        result = (
            self.sheets.values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{STAFF_SHEET}'!A1:H100",
            )
            .execute()
        )
        values = result.get("values", [])
        if not values:
            return []

        staff = []
        for row in values[1:]:
            if not row or not str(row[0]).strip():
                continue
            nick = resolve_email_to_nick(str(row[0]).strip())
            if len(row) == 1 or (len(row) > 1 and not str(row[1]).strip()):
                staff.append({"section": nick})
            else:
                email = STAFF_EMAILS.get(nick.lower(), "")
                if not email:
                    full_text = f"{row[3] if len(row) > 3 else ''} {row[4] if len(row) > 4 else ''}"
                    m = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", full_text)
                    if m:
                        email = m.group(0)

                entry = {
                    "nick": nick,
                    "position": str(row[1]).strip() if len(row) > 1 else "",
                    "discord": str(row[2]).strip() if len(row) > 2 else "",
                    "telegram": str(row[3]).strip() if len(row) > 3 else "",
                    "comment": str(row[4]).strip() if len(row) > 4 else "",
                    "invited": str(row[5]).strip() if len(row) > 5 else "0",
                    "hours": str(row[6]).strip() if len(row) > 6 else "0",
                    "warns": str(row[7]).strip() if len(row) > 7 else "0",
                    "email": email,
                }
                staff.append(entry)
        return staff
