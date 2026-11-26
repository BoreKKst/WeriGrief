"""
Google Sheets Monitor for WeriTime Staff Bot.
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
from zoneinfo import ZoneInfo
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

log = logging.getLogger("weritime_staff.sheets")

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
        self.custom_required_staff: list[str] = []  # nicks forced to complete daily norm
        self.mc_playtime_snapshots: dict[str, dict] = {}  # { nick_low: { "hours": float, "date": str, "timestamp": float } }
        self.vacations: dict[str, dict] = {}
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
        try:
            self._load_vacations_from_sheet()
        except Exception as e:
            log.warning("Could not read the vacation register from Google Sheets: %s", e)

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
                if "custom_required_staff" in data:
                    self.custom_required_staff = data.get("custom_required_staff", [])
                if "mc_playtime_snapshots" in data:
                    self.mc_playtime_snapshots = data.get("mc_playtime_snapshots", {})
                if "vacations" in data:
                    self.vacations = data.get("vacations", {})
                log.info(f"Loaded state: {len(self.known_warnings)} warnings, {len(self.known_staff)} staff, {len(self.dismissed_history)} dismissed records, month: {self.last_reset_month}, exempt: {len(self.custom_exempt_staff)}")
            except Exception as e:
                log.warning(f"Failed to load state: {e}")
                self.known_warnings = []
                self.known_staff = {}
                self.dismissed_history = []
                self.last_reset_month = ""
                self.last_playtime_date = ""
                self.custom_exempt_staff = []
                self.custom_required_staff = []
                self.mc_playtime_snapshots = {}
                self.vacations = {}

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
                        "custom_required_staff": self.custom_required_staff,
                        "mc_playtime_snapshots": self.mc_playtime_snapshots,
                        "vacations": self.vacations,
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
                warns_cleared = self.clear_player_warnings(old_copy["nick"], update_staff_count=False)
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

        # Google Sheets is the source of truth: refresh the complete local comparison
        # snapshot on every poll, even when no personnel changes were detected.
        self.known_staff = current_map
        self._save_state()

        return {
            "appointed": appointed,
            "promoted": promoted,
            "removed": removed,
        }

    def remove_staff_member(self, target_nick: str, removed_by: str = "", reason: str = "") -> dict | None:
        """
        Атомарно удаляет сотрудника и его предупреждения из Google Sheets.
        Сохраняет историю снятия после успешного изменения таблиц.
        """
        try:
            target_key = target_nick.strip().replace("_", "").casefold()
            if not target_key:
                return None

            current_staff = self.get_staff_list()
            target_entry = None

            for staff in current_staff:
                staff_nick = str(staff.get("nick", "")).strip()
                if staff_nick.replace("_", "").casefold() == target_key:
                    target_entry = staff
                    break

            if not target_entry:
                return None

            actual_nick = str(target_entry["nick"]).strip()
            actual_key = actual_nick.replace("_", "").casefold()

            warnings_history = []
            for warning_row in self._fetch_warnings():
                if not warning_row:
                    continue
                row_nick = str(warning_row[0] if len(warning_row) > 0 else "").strip()
                if row_nick.replace("_", "").casefold() == actual_key:
                    warnings_history.append({
                        "nick": row_nick or actual_nick,
                        "date": str(warning_row[1] if len(warning_row) > 1 else "—"),
                        "reason": str(warning_row[2] if len(warning_row) > 2 else "Причина не указана"),
                        "issued_by": str(warning_row[3] if len(warning_row) > 3 else "Неизвестно"),
                    })

            staff_result = self.sheets.values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{STAFF_SHEET}'!A:A",
            ).execute()

            staff_rows = staff_result.get("values", [])
            staff_row_number = None

            for row_number, row in enumerate(staff_rows, 1):
                if not row:
                    continue
                row_nick = resolve_email_to_nick(str(row[0]).strip())
                if row_nick.replace("_", "").casefold() == actual_key:
                    staff_row_number = row_number
                    break

            if staff_row_number is None:
                log.error("Dismissal aborted: staff row not found for %s", actual_nick)
                return None

            warning_result = self.sheets.values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{WARNINGS_SHEET}'!A:D",
                valueRenderOption="FORMATTED_VALUE",
            ).execute()

            warning_rows = warning_result.get("values", [])
            warning_row_numbers = []

            for row_number, row in enumerate(warning_rows, 1):
                if row_number == 1 and row:
                    header = str(row[0]).strip().casefold()
                    if header in ("игровой никнейм", "никнейм"):
                        continue

                row_nick = str(row[0] if row else "").strip()
                if row_nick.replace("_", "").casefold() == actual_key:
                    warning_row_numbers.append(row_number)

            metadata = self.sheets.get(
                spreadsheetId=self.spreadsheet_id,
                fields="sheets(properties(sheetId,title))",
            ).execute()

            sheet_ids = {
                item["properties"]["title"]: item["properties"]["sheetId"]
                for item in metadata.get("sheets", [])
            }

            if STAFF_SHEET not in sheet_ids or WARNINGS_SHEET not in sheet_ids:
                log.error("Dismissal aborted: required sheet IDs were not found")
                return None

            requests = []

            for row_number in sorted(warning_row_numbers, reverse=True):
                requests.append({
                    "deleteDimension": {
                        "range": {
                            "sheetId": sheet_ids[WARNINGS_SHEET],
                            "dimension": "ROWS",
                            "startIndex": row_number - 1,
                            "endIndex": row_number,
                        }
                    }
                })

            requests.append({
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_ids[STAFF_SHEET],
                        "dimension": "ROWS",
                        "startIndex": staff_row_number - 1,
                        "endIndex": staff_row_number,
                    }
                }
            })

            self.sheets.batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": requests},
            ).execute()

            log.info(
                "Atomically deleted staff member %s and %s warning rows",
                actual_nick,
                len(warning_row_numbers),
            )

            dismiss_entry = {
                "nick": actual_nick,
                "position": target_entry.get("position", "—"),
                "discord": target_entry.get("discord", "—"),
                "email": target_entry.get("email", ""),
                "removed_by": resolve_email_to_nick(removed_by) if removed_by else "Руководство проекта",
                "reason": reason,
                "dismissed_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
                "warns_cleared": len(warning_row_numbers),
                "warnings_history": warnings_history,
            }

            self.dismissed_history.append(dismiss_entry)
            self.known_staff.pop(actual_nick.lower(), None)

            if hasattr(self, "known_warnings"):
                if isinstance(self.known_warnings, dict):
                    self.known_warnings = {
                        key: value
                        for key, value in self.known_warnings.items()
                        if str(key).split("|", 1)[0].replace("_", "").casefold() != actual_key
                    }
                else:
                    self.known_warnings = [
                        key for key in self.known_warnings
                        if str(key).split("|", 1)[0].replace("_", "").casefold() != actual_key
                    ]

            self._save_state()
            return dismiss_entry

        except Exception as e:
            log.exception("Error removing staff member %s: %s", target_nick, e)
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
                    return None

            # A nick, B position, C Discord username, D Telegram/VK,
            # E played hours, F warnings (matches the current Google Sheet).
            row_data = [
                nick_clean,
                position.strip(),
                discord_name.strip().lstrip("@"),
                telegram.strip(),
                "0",  # played hours
                "0",  # warnings
                datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d"),  # appointment date
            ]

            body = {"values": [row_data]}
            self.sheets.values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{STAFF_SHEET}'!A:G",
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
                "appointed_at": datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d"),
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

            # Column F ('Предупреждения') is a COUNTIF formula over 'Учет предупреждений';
            # Google Sheets recalculates the count automatically after clearing warning rows.

            # 2. Update state
            self.known_warnings = []
            self.last_reset_month = datetime.now(timezone.utc).strftime("%Y-%m")
            self._save_state()

            log.info("Monthly warnings reset completed successfully!")
            return True
        except Exception as e:
            log.error(f"Failed to reset monthly warnings: {e}")
            return False

    def clear_player_warnings(self, target_nick: str, update_staff_count: bool = True) -> int | None:
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
                if update_staff_count and not self._update_staff_warning_count(target_nick, 0):
                    return None
                return 0

            header = values[0]
            data_rows = values[1:]

            target_no_us = target_low.replace("_", "").casefold()
            kept_rows = []
            removed_count = 0

            for r in data_rows:
                if not r:
                    continue

                r_nick = str(r[0]).strip() if len(r) > 0 else ""
                r_key = r_nick.replace("_", "").casefold()

                if r_key == target_no_us:
                    removed_count += 1
                else:
                    # Сохраняем даже неполные записи других сотрудников.
                    kept_rows.append([str(cell) for cell in r])

            if removed_count == 0:
                if update_staff_count and not self._update_staff_warning_count(target_nick, 0):
                    return None
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

            if update_staff_count and not self._update_staff_warning_count(target_nick, 0):
                return None
            log.info(f"Cleared {removed_count} warning(s) for player {target_nick}")
            return removed_count
        except Exception as e:
            log.error(f"Error clearing warnings for {target_nick}: {e}")
            return None

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

            self._update_staff_warning_count(target_nick, remaining_count)
            log.info(f"Removed warning #{case_num} for {target_nick}: {removed_info['reason']} (remaining: {remaining_count})")
            return True, removed_info, remaining_count
        except Exception as e:
            log.error(f"Error removing warning for {target_nick}: {e}")
            return False, None, 0

    def increment_staff_hours(self, deltas: dict[str, float]) -> dict[str, float]:
        """
        deltas: { nick_low: delta_hours_float }
        Reads current hours in Column E of 'Списки', adds delta_hours,
        and updates Column E with the new sum.
        Returns { nick_low: new_hours_float }
        """
        try:
            res = (
                self.sheets.values()
                .get(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{STAFF_SHEET}'!A:E",
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
                        curr_str = str(r[4]).strip() if len(r) > 4 and r[4] else "0"
                        try:
                            curr_val = float(curr_str.replace(",", "."))
                        except ValueError:
                            curr_val = 0.0
                        new_val = max(0.0, curr_val + delta)
                        result_map[nick] = new_val
                        # Format as int if whole number, else 1 decimal place
                        val_str = str(int(round(new_val))) if abs(new_val - round(new_val)) < 0.05 else f"{new_val:.1f}"
                        data.append({
                            "range": f"'{STAFF_SHEET}'!E{idx}",
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

    def _canonical_norm_nick(self, nick: str) -> str:
        nick_low = nick.strip().lower()
        target_no_us = nick_low.replace("_", "")
        for staff in self.get_staff_list():
            staff_nick = (staff.get("nick") or "").strip().lower()
            if staff_nick == nick_low or staff_nick.replace("_", "") == target_no_us:
                return staff_nick
        return nick_low

    def set_norm_exempt(self, nick: str, is_exempt: bool) -> bool:
        """Переключает ручное освобождение/обязательную норму."""
        canonical = self._canonical_norm_nick(nick)
        target = canonical.replace("_", "")
        before = (list(self.custom_exempt_staff), list(self.custom_required_staff))

        self.custom_exempt_staff = [
            x for x in self.custom_exempt_staff
            if x.strip().lower() != canonical
            and x.strip().lower().replace("_", "") != target
        ]
        self.custom_required_staff = [
            x for x in self.custom_required_staff
            if x.strip().lower() != canonical
            and x.strip().lower().replace("_", "") != target
        ]

        if is_exempt:
            self.custom_exempt_staff.append(canonical)
        else:
            self.custom_required_staff.append(canonical)

        changed = before != (
            self.custom_exempt_staff, self.custom_required_staff
        )
        if changed:
            self._save_state()
            log.info(
                "Updated norm setting for %s: exempt=%s",
                canonical, is_exempt
            )
        return changed

    def is_custom_exempt(self, nick: str) -> bool:
        if not nick:
            return False
        target = nick.strip().lower().replace("_", "")
        return any(
            target == entry.strip().lower().replace("_", "")
            for entry in self.custom_exempt_staff
        )

    def is_custom_required(self, nick: str) -> bool:
        if not nick:
            return False
        target = nick.strip().lower().replace("_", "")
        return any(
            target == entry.strip().lower().replace("_", "")
            for entry in self.custom_required_staff
        )

    def update_multiple_staff_hours(self, hours_map: dict[str, str]) -> int:
        """
        hours_map: { nick_low: hours_str }
        Updates Column E ('Кол-во отыгранных часов') in 'Списки' for all matching staff.
        """
        try:
            res = (
                self.sheets.values()
                .get(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{STAFF_SHEET}'!A:A",
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
                            "range": f"'{STAFF_SHEET}'!E{idx}",
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

    def _update_staff_warning_count(self, nick: str, count: int) -> bool:
        """Synchronizes warning count to column F without touching columns A:E."""
        try:
            target = nick.strip().lower()
            target_compact = target.replace("_", "")
            result = self.sheets.values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{STAFF_SHEET}'!A:F",
            ).execute()
            for row_idx, row in enumerate(result.get("values", [])[1:], start=2):
                if not row or not str(row[0]).strip():
                    continue
                current = resolve_email_to_nick(str(row[0]).strip()).lower()
                if current == target or current.replace("_", "") == target_compact:
                    self.sheets.values().update(
                        spreadsheetId=self.spreadsheet_id,
                        range=f"'{STAFF_SHEET}'!F{row_idx}",
                        valueInputOption="USER_ENTERED",
                        body={"values": [[str(max(0, int(count)))]]},
                    ).execute()
                    return True
            return False
        except Exception as exc:
            log.error("Failed to synchronize warning count for %s: %s", nick, exc)
            return False

    def add_warning_once(
        self, nick: str, date: str, reason: str, issuer: str
    ) -> tuple[int, bool, bool]:
        """Гарантирует одно автоматическое предупреждение на игрока за день."""
        try:
            norm_date = normalize_date_str(date)
            nick_key = nick.strip().casefold()
            issuer_key = issuer.strip().casefold()

            def is_same_automatic_warning(row):
                return (
                    _is_complete_warn_row(row)
                    and str(row[0]).strip().casefold() == nick_key
                    and normalize_date_str(str(row[1])) == norm_date
                    and str(row[3]).strip().casefold() == issuer_key
                )

            rows = self._fetch_warnings()
            existing_row = next(
                (row for row in rows if is_same_automatic_warning(row)),
                None,
            )
            created = existing_row is None

            if created:
                self.add_warning(nick, norm_date, reason, issuer)
                rows = self._fetch_warnings()
                existing_row = next(
                    (row for row in rows if is_same_automatic_warning(row)),
                    None,
                )

                if existing_row is None:
                    log.error(
                        "Automatic warning was not confirmed in Sheets: %s %s",
                        nick, norm_date,
                    )
                    return 0, False, False

            # Синхронизируем локальный список, чтобы повторно не обработать
            # уже существующую строку при следующем опросе таблицы.
            known_key = self._row_key(existing_row)
            if known_key not in self.known_warnings:
                self.known_warnings.append(known_key)
                self._save_state()

            total = sum(
                1 for row in rows
                if _is_complete_warn_row(row)
                and str(row[0]).strip().casefold() == nick_key
            )

            synced = self._update_staff_warning_count(nick, total)
            if synced is False:
                log.error(
                    "Could not synchronize warning count for %s",
                    nick,
                )
                return total, created, False

            return total, created, True

        except Exception:
            log.exception(
                "Failed to ensure automatic warning for %s on %s",
                nick, date,
            )
            return 0, False, False

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

            self._update_staff_warning_count(nick, total_player_warns)
            log.info(f"Added warning for {nick}: {reason} (by {issuer}, total: {total_player_warns})")
            return total_player_warns
        except Exception as e:
            log.error(f"Error adding warning for {nick}: {e}")
            return 0

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

    def update_staff_position(self, target_nick: str, new_position: str) -> dict | None:
        """Меняет только колонку B (должность) у сотрудника в листе «Списки»."""
        try:
            target_low = target_nick.strip().lower()
            result = self.sheets.values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{STAFF_SHEET}'!A:B",
            ).execute()
            rows = result.get("values", [])
            for row_idx, row in enumerate(rows[1:], start=2):
                if row and resolve_email_to_nick(str(row[0]).strip()).lower() == target_low:
                    old_position = str(row[1]).strip() if len(row) > 1 else ""
                    self.sheets.values().update(
                        spreadsheetId=self.spreadsheet_id,
                        range=f"'{STAFF_SHEET}'!B{row_idx}",
                        valueInputOption="USER_ENTERED",
                        body={"values": [[new_position]]},
                    ).execute()
                    updated = next((x for x in self.get_staff_list()
                                    if x.get("nick", "").lower() == target_low), None)
                    if updated:
                        self.known_staff[target_low] = updated
                    self._save_state()
                    return {"nick": target_nick, "old_position": old_position,
                            "new_position": new_position}
            return None
        except Exception as e:
            log.error(f"Failed to update staff position for {target_nick}: {e}")
            return None

    def _load_vacations_from_sheet(self):
        """Loads vacation entries from the dedicated Google Sheets tab when present."""
        metadata = self.sheets.get(spreadsheetId=self.spreadsheet_id).execute()
        if not any(item.get("properties", {}).get("title") == "Отпуска WeriTime"
                   for item in metadata.get("sheets", [])):
            return
        result = self.sheets.values().get(
            spreadsheetId=self.spreadsheet_id,
            range="'Отпуска WeriTime'!A:E",
        ).execute()
        vacations = {}
        for row in result.get("values", [])[1:]:
            if len(row) < 3 or not str(row[0]).strip():
                continue
            nick = str(row[0]).strip()
            try:
                def parse_sheet_date(raw):
                    try:
                        return datetime.fromisoformat(str(raw).strip()).date().isoformat()
                    except ValueError:
                        normalized = normalize_date_str(raw)
                        return datetime.strptime(normalized, "%d.%m.%Y").date().isoformat()

                entry = {
                    "start": parse_sheet_date(row[1]),
                    "end": parse_sheet_date(row[2]),
                    "days": int(row[3]) if len(row) > 3 and str(row[3]).strip() else 0,
                    "created_at": str(row[4]).strip() if len(row) > 4 else "",
                }
            except (ValueError, TypeError):
                continue
            vacations[nick.lower()] = entry
        if vacations:
            self.vacations.update(vacations)

    def _sync_vacations_to_sheet(self):
        """Creates/updates the WeriTime vacation tab without changing other tabs."""
        metadata = self.sheets.get(spreadsheetId=self.spreadsheet_id).execute()
        sheet_id = next((
            item.get("properties", {}).get("sheetId")
            for item in metadata.get("sheets", [])
            if item.get("properties", {}).get("title") == "Отпуска WeriTime"
        ), None)
        if sheet_id is None:
            response = self.sheets.batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": "Отпуска WeriTime"}}}]},
            ).execute()
            sheet_id = response["replies"][0]["addSheet"]["properties"]["sheetId"]
        rows = [["Minecraft-ник", "Начало", "Окончание", "Дней", "Оформлено"]]
        for nick, entry in sorted(self.vacations.items()):
            rows.append([
                nick,
                entry.get("start", ""),
                entry.get("end", ""),
                entry.get("days", ""),
                entry.get("created_at", ""),
            ])
        sheet_range = "'Отпуска WeriTime'!A:E"
        self.sheets.values().clear(
            spreadsheetId=self.spreadsheet_id,
            range=sheet_range,
            body={},
        ).execute()
        self.sheets.values().update(
            spreadsheetId=self.spreadsheet_id,
            range="'Отпуска WeriTime'!A1",
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()
        log.info("Synced %s vacation entries to Google Sheets", len(rows) - 1)

    def create_vacation(self, nick: str, days: int) -> dict:
        """Отпуск на 1–15 дней; повторный отпуск доступен через 13 дней после окончания."""
        from datetime import date, timedelta
        key = nick.strip().lower()
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        current = self.vacations.get(key, {})
        end_raw = current.get("end")
        if current.get("start") and end_raw:
            try:
                previous_end = date.fromisoformat(end_raw)
                if today <= previous_end:
                    raise ValueError(f"Отпуск уже активен до {previous_end.strftime('%d.%m.%Y')}")
                next_allowed = previous_end + timedelta(days=13)
                if today < next_allowed:
                    raise ValueError(f"Повторный отпуск доступен с {next_allowed.strftime('%d.%m.%Y')}")
            except ValueError as exc:
                if str(exc).startswith(("Отпуск уже", "Повторный")):
                    raise
        if not 1 <= int(days) <= 15:
            raise ValueError("Можно оформить от 1 до 15 дней.")
        end_date = today + timedelta(days=int(days) - 1)
        entry = {"start": today.isoformat(), "end": end_date.isoformat(),
                 "days": int(days), "created_at": datetime.now(timezone.utc).isoformat()}
        previous = self.vacations.get(key)
        self.vacations[key] = entry
        try:
            self._sync_vacations_to_sheet()
        except Exception as exc:
            if previous is None:
                self.vacations.pop(key, None)
            else:
                self.vacations[key] = previous
            raise RuntimeError("Не удалось сохранить отпуск в Google Sheets. Изменения отменены.") from exc
        self._save_state()
        return entry

    def is_on_vacation(self, nick: str, on_date=None) -> bool:
        from datetime import date
        entry = self.vacations.get(nick.strip().lower(), {})
        try:
            current = on_date or datetime.now(ZoneInfo("Europe/Moscow")).date()
            return date.fromisoformat(entry["start"]) <= current <= date.fromisoformat(entry["end"])
        except Exception:
            return False

    def get_vacation(self, nick: str) -> dict | None:
        return self.vacations.get(nick.strip().lower())

    def clear_vacation(self, nick: str) -> bool:
        """Удаляет отпуск сотрудника из сохранённого состояния."""
        key = nick.strip().lower()
        if key not in self.vacations:
            return False
        previous = self.vacations.pop(key)
        try:
            self._sync_vacations_to_sheet()
        except Exception as exc:
            self.vacations[key] = previous
            raise RuntimeError("Не удалось обновить Google Sheets. Отпуск оставлен без изменений.") from exc
        self._save_state()
        return True

    def update_staff_discord_username(self, target_nick: str, username: str) -> bool:
        """Write current Discord username (not numeric ID) into column C."""
        try:
            target_low = target_nick.strip().lower()
            username = username.strip().lstrip("@")
            if not username:
                return False
            result = self.sheets.values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{STAFF_SHEET}'!A:C",
            ).execute()
            for row_idx, row in enumerate(result.get("values", [])[1:], start=2):
                if row and resolve_email_to_nick(str(row[0]).strip()).lower() == target_low:
                    current = str(row[2]).strip().lstrip("@") if len(row) > 2 else ""
                    if current == username:
                        return True
                    self.sheets.values().update(
                        spreadsheetId=self.spreadsheet_id,
                        range=f"'{STAFF_SHEET}'!C{row_idx}",
                        valueInputOption="RAW",
                        body={"values": [[username]]},
                    ).execute()
                    return True
            return False
        except Exception as e:
            log.error(f"Failed to update Discord username for {target_nick}: {e}")
            return False

    def get_staff_list(self) -> list[dict]:
        """Fetch current staff list from 'Списки' sheet (A:F layout)."""
        result = (
            self.sheets.values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{STAFF_SHEET}'!A:G",
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
                    "discord": str(row[2]).strip().lstrip("@") if len(row) > 2 else "",
                    "telegram": str(row[3]).strip() if len(row) > 3 else "",
                    "hours": str(row[4]).strip() if len(row) > 4 else "0",
                    "warns": str(row[5]).strip() if len(row) > 5 else "0",
                    "appointed_at": str(row[6]).strip() if len(row) > 6 else "",
                    "email": email,
                }
                staff.append(entry)
        return staff
