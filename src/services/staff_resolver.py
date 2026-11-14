"""
Staff target resolution (query/member/mention/email -> MC nickname and Discord member)
and Google Sheets permission check helpers.
"""

import re
import discord
from src.core.config import (
    log, STAFF_REGISTRY, LEADERSHIP_IDS, LEADERSHIP_ROLES, ADMIN_KEYWORDS
)
from src.services.sheets_api import STAFF_EMAILS, resolve_email_to_nick


class StaffResolver:
    """Класс для разрешения запросов (ник/ID/mention/email) в сотрудника Discord/Minecraft."""

    def __init__(self, sheets_monitor=None):
        self.sheets_monitor = sheets_monitor
        self.sheets_discord_map: dict[str, str] = {}  # mc_nick.lower() -> discord_username

    def populate_sheets_discord_map(self):
        """Кэширует соответствие ников Minecraft и Discord-юзернеймов из таблицы 'Списки'."""
        if self.sheets_monitor:
            try:
                staff = self.sheets_monitor.get_staff_list()
                # Replace cache, do not accumulate removed/renamed staff from old snapshots.
                self.sheets_discord_map = {}
                for s in staff:
                    nick = s.get("nick", "").strip().lower()
                    disc = s.get("discord", "").strip().lower().replace("\n", "")
                    if nick and disc:
                        self.sheets_discord_map[nick] = disc
                log.info(f"Sheets Discord map updated: {len(self.sheets_discord_map)} entries")
            except Exception as e:
                log.error(f"Failed to populate Sheets Discord map: {e}")

    async def resolve_discord_ids(self, guild: discord.Guild):
        """Сопоставляет статический реестр STAFF_REGISTRY с Discord Member ID на сервере."""
        for mc_nick, info in STAFF_REGISTRY.items():
            dname = info["discord_name"].lower()
            for member in guild.members:
                mname = member.name.lower()
                dname_m = member.display_name.lower() if member.display_name else ""
                gname = member.global_name.lower() if hasattr(member, 'global_name') and member.global_name else ""
                if dname in (mname, dname_m, gname) or str(member).lower() == dname:
                    info["discord_id"] = member.id
                    log.info(f"Resolved {mc_nick} -> {member} (ID: {member.id})")
                    break

    def find_member_by_mc_nick(self, guild: discord.Guild, mc_nick: str) -> discord.Member | None:
        """Ищет участника Discord по никнейму Minecraft."""
        if not guild or not mc_nick:
            return None
        low = mc_nick.strip().lower()

        # 1. Поиск в статическом реестре
        if low in STAFF_REGISTRY and STAFF_REGISTRY[low]["discord_id"]:
            return guild.get_member(STAFF_REGISTRY[low]["discord_id"])

        # 2. Поиск в кэше Google Sheets
        if low not in self.sheets_discord_map:
            self.populate_sheets_discord_map()

        discord_name = self.sheets_discord_map.get(low, "").lower()
        if discord_name:
            for member in guild.members:
                mname = member.name.lower()
                dname = member.display_name.lower() if member.display_name else ""
                gname = member.global_name.lower() if hasattr(member, 'global_name') and member.global_name else ""
                if discord_name in (mname, dname, gname) or discord_name.replace("_", "") in (
                    mname.replace("_", ""), dname.replace("_", ""), gname.replace("_", "")
                ):
                    return member

        # 3. Прямое совпадение никнеймов
        clean_low = low.replace("_", "")
        for member in guild.members:
            mname = member.name.lower()
            dname = member.display_name.lower() if member.display_name else ""
            gname = member.global_name.lower() if hasattr(member, 'global_name') and member.global_name else ""
            if low in (mname, dname, gname) or clean_low in (
                mname.replace("_", ""), dname.replace("_", ""), gname.replace("_", "")
            ):
                return member

        return None

    def resolve_query_to_target(
        self, guild: discord.Guild, query: str | discord.Member
    ) -> tuple[str, discord.Member | None]:
        """
        Преобразует любой ввод (Member, упоминание <@...>, Discord имя, ник MC, email)
        в кортеж: (mc_nickname, discord_member_or_none).
        """
        if isinstance(query, discord.Member):
            member = query
            mname = member.name.lower()
            gname = (member.global_name or "").lower()
            dname = (member.display_name or "").lower()

            if self.sheets_monitor:
                try:
                    for s in self.sheets_monitor.get_staff_list():
                        mc = s.get("nick")
                        disc = (s.get("discord") or "").lower()
                        if mc and disc:
                            disc_no_us = disc.replace("_", "")
                            if disc in (mname, gname, dname) or disc_no_us in (
                                mname.replace("_", ""), dname.replace("_", ""), gname.replace("_", "")
                            ):
                                return mc, member
                except Exception:
                    pass

            for mc, info in STAFF_REGISTRY.items():
                disc_n = info["discord_name"].lower()
                if info.get("discord_id") == member.id or disc_n in (mname, gname, dname) or disc_n.replace("_", "") in (
                    mname.replace("_", ""), dname.replace("_", ""), gname.replace("_", "")
                ):
                    return mc, member

            if not self.sheets_discord_map:
                self.populate_sheets_discord_map()
            for mc, disc in self.sheets_discord_map.items():
                if disc in (mname, gname, dname) or disc.replace("_", "") in (
                    mname.replace("_", ""), dname.replace("_", ""), gname.replace("_", "")
                ):
                    return mc, member

            return member.name, member

        q = str(query).strip()

        # Формат упоминания <@123...>
        match = re.match(r"^<@!?(\d+)>$", q)
        if match:
            user_id = int(match.group(1))
            member = guild.get_member(user_id) if guild else None
            if member:
                return self.resolve_query_to_target(guild, member)

        # Числовой ID
        if q.isdigit():
            member = guild.get_member(int(q)) if guild else None
            if member:
                return self.resolve_query_to_target(guild, member)

        clean_q = re.sub(r"^@|\[.*?\]\s*", "", q).strip().lower()
        clean_q_no_us = clean_q.replace("_", "")

        # Поиск по email
        resolved_nick = resolve_email_to_nick(clean_q)
        if resolved_nick != clean_q:
            member = self.find_member_by_mc_nick(guild, resolved_nick)
            return resolved_nick, member

        for nick_k, email in STAFF_EMAILS.items():
            if email.lower() == clean_q or (clean_q in email.lower() and "@" in clean_q):
                member = self.find_member_by_mc_nick(guild, nick_k)
                return nick_k, member

        # Поиск по списку персонала Google Sheets
        if self.sheets_monitor:
            try:
                for s in self.sheets_monitor.get_staff_list():
                    s_nick = s.get("nick")
                    s_disc = (s.get("discord") or "").lower()
                    s_email = (s.get("email") or "").lower()
                    if s_nick:
                        sn_low = s_nick.lower()
                        sn_no_us = sn_low.replace("_", "")
                        if clean_q in (sn_low, s_disc) or clean_q_no_us in (sn_no_us, s_disc.replace("_", "")) or (
                            s_email and clean_q in (s_email,)
                        ):
                            member = self.find_member_by_mc_nick(guild, s_nick)
                            return s_nick, member
            except Exception:
                pass

        # Поиск по STAFF_REGISTRY
        for mc, info in STAFF_REGISTRY.items():
            disc_n = info["discord_name"].lower()
            if clean_q in (mc.lower(), disc_n) or clean_q_no_us in (mc.lower().replace("_", ""), disc_n.replace("_", "")):
                member = guild.get_member(info["discord_id"]) if (guild and info["discord_id"]) else self.find_member_by_mc_nick(guild, mc)
                return mc, member

        # Поиск по sheets_discord_map
        if not self.sheets_discord_map:
            self.populate_sheets_discord_map()
        for mc, disc in self.sheets_discord_map.items():
            if clean_q in (mc.lower(), disc.lower(), disc.replace("_", "")) or clean_q_no_us in (
                mc.lower().replace("_", ""), disc.replace("_", "")
            ):
                member = self.find_member_by_mc_nick(guild, mc)
                return mc, member

        # Поиск среди участников сервера по имени
        if guild:
            for member in guild.members:
                mname = member.name.lower()
                dname = member.display_name.lower() if member.display_name else ""
                gname = member.global_name.lower() if hasattr(member, 'global_name') and member.global_name else ""
                if clean_q in (mname, dname, gname):
                    return self.resolve_query_to_target(guild, member)

        member = self.find_member_by_mc_nick(guild, q)
        return q, member

    def get_staff_entry_for_member(self, member: discord.Member) -> dict | None:
        """
        Возвращает запись персонала из Google Sheets ('Списки') для участника Discord.
        Если человека нет в таблице Google Таблицы, возвращает None (защита от несанкционированного доступа).
        """
        if not isinstance(member, (discord.Member, discord.User)) or not self.sheets_monitor:
            return None

        try:
            staff_list = self.sheets_monitor.get_staff_list()
        except Exception as e:
            log.error(f"Error fetching staff list in get_staff_entry_for_member: {e}")
            return None

        m_id = getattr(member, "id", None)
        m_name = (getattr(member, "name", "") or "").lower().strip()
        m_global = (getattr(member, "global_name", "") or "").lower().strip()
        m_display = (getattr(member, "display_name", "") or "").lower().strip()

        registry_nicks = set()
        for mc, reg_info in STAFF_REGISTRY.items():
            if (m_id and reg_info.get("discord_id") == m_id) or reg_info.get("discord_name", "").lower() in (
                m_name, m_global, m_display
            ):
                registry_nicks.add(mc.lower())

        for s in staff_list:
            s_nick = (s.get("nick") or "").strip()
            if not s_nick:
                continue
            s_nick_low = s_nick.lower()
            s_disc = (s.get("discord") or "").strip().lower().replace("@", "")

            if m_id and s_disc == str(m_id):
                return s

            if s_nick_low in registry_nicks:
                return s

            if s_disc:
                candidates = [m_name, m_global, m_display]
                if s_disc in candidates or any(c and s_disc == c for c in candidates):
                    return s
                clean_s_disc = s_disc.replace("_", "")
                if any(c and clean_s_disc == c.replace("_", "") for c in candidates):
                    return s
                if any(c and (s_disc in c or clean_s_disc in c.replace("_", "")) for c in candidates):
                    return s

            candidates_nick = [m_name, m_global, m_display]
            if s_nick_low in candidates_nick:
                return s
            clean_s_nick = s_nick_low.replace("_", "")
            if any(c and clean_s_nick == c.replace("_", "") for c in candidates_nick):
                return s
            if any(c and (s_nick_low in c or clean_s_nick in c.replace("_", "")) for c in candidates_nick):
                return s

        return None

    def is_leadership(self, member: discord.Member, guild: discord.Guild = None) -> bool:
        """
        Проверяет, является ли пользователь Руководством (Владелец, Со-владелец, Куратор).
        ТРЕБОВАНИЕ: обязательное наличие записи в Google Sheets с соответствующей должностью или в LEADERSHIP_IDS.
        """
        if not isinstance(member, (discord.Member, discord.User)):
            return False
        if member.id in LEADERSHIP_IDS:
            return True

        staff_entry = self.get_staff_entry_for_member(member)
        if not staff_entry:
            return False

        pos = (staff_entry.get("position") or "").lower()
        return any(term in pos for term in LEADERSHIP_ROLES)

    def is_admin_or_higher(self, member: discord.Member, guild: discord.Guild = None) -> bool:
        """
        Проверяет, является ли пользователь Администратором (Старший, Обычный, Куратор, Владелец).
        ТРЕБОВАНИЕ: обязательное наличие записи в Google Sheets или в LEADERSHIP_IDS.
        """
        if not isinstance(member, (discord.Member, discord.User)):
            return False
        if member.id in LEADERSHIP_IDS:
            return True

        staff_entry = self.get_staff_entry_for_member(member)
        if not staff_entry:
            return False

        pos = (staff_entry.get("position") or "").lower()
        return any(term in pos for term in ADMIN_KEYWORDS) or any(term in pos for term in LEADERSHIP_ROLES)

