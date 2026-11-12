"""
Discord roles management and Minecraft LuckPerms permission synchronization via Pterodactyl.
"""

import re
import aiohttp
import discord
from src.core.config import (
    log, PTERO_API_KEY, PTERO_BASE_URL, PTERO_SERVER_IDS
)

# Роли персонала WeriGrief. Project Team — только Discord, без группы LuckPerms.
ROLE_CREATOR = 1554582347872079914
ROLE_GL_OWNER = 1569338896448356372
ROLE_OWNER = 1554582667000021125
ROLE_GL_CURATOR = 1554582767965311068
ROLE_CURATOR = 1569194858982285433
ROLE_Z_CURATOR = 1569195073814405140
ROLE_GL_ADMIN = 1554582998928859176
ROLE_ADMIN = 1554583247902605312
ROLE_GL_MODER = 1554583311568085113
ROLE_ST_MODER = 1570415032091807815
ROLE_MODER = 1554583443650912409
ROLE_ML_MODER = 1569198191541362841
ROLE_HELPER = 1554583527486390272
ROLE_PROJECT_TEAM = 1564330724482355330

STAFF_ROLE_IDS = {
    ROLE_CREATOR, ROLE_GL_OWNER, ROLE_OWNER, ROLE_GL_CURATOR, ROLE_CURATOR,
    ROLE_Z_CURATOR, ROLE_GL_ADMIN, ROLE_ADMIN, ROLE_GL_MODER, ROLE_ST_MODER,
    ROLE_MODER, ROLE_ML_MODER, ROLE_HELPER, ROLE_PROJECT_TEAM,
}

STAFF_ROLE_KEYWORDS = (
    "creator", "gl.owner", "owner", "curator", "gadmin", "admin",
    "moder", "helper", "project team", "персонал", "руководство",
)

# Отдельной базовой роли персонала в новой системе нет.
BASE_STAFF_ROLE_ID = 0

POSITION_DISCORD_ROLES_MAP = {
    "creator": ROLE_CREATOR, "devolper": ROLE_CREATOR, "developer": ROLE_CREATOR,
    "gl.owner": ROLE_GL_OWNER, "gowner": ROLE_GL_OWNER,
    "owner": ROLE_OWNER,
    "gl.curator": ROLE_GL_CURATOR, "gcurator": ROLE_GL_CURATOR,
    "curator": ROLE_CURATOR,
    "z.curator": ROLE_Z_CURATOR, "zcurator": ROLE_Z_CURATOR,
    "gl.admin": ROLE_GL_ADMIN, "gadmin": ROLE_GL_ADMIN,
    "admin": ROLE_ADMIN,
    "gl.moder": ROLE_GL_MODER, "glmoder": ROLE_GL_MODER,
    "st.moder": ROLE_ST_MODER, "stmoder": ROLE_ST_MODER,
    "moder": ROLE_MODER,
    "ml.moder": ROLE_ML_MODER, "mlmoder": ROLE_ML_MODER,
    "helper": ROLE_HELPER, "хелпер": ROLE_HELPER,
    "project team": ROLE_PROJECT_TEAM,
}

# Группы LuckPerms: madmin и mladmin существуют только в Minecraft.
ALL_LP_GROUPS = {
    "devolper", "gowner", "owner", "gcurator", "curator", "zcurator",
    "gadmin", "admin", "madmin", "glmoder", "stmoder", "moder",
    "mlmoder", "mladmin", "helper", "default",
}

POSITION_LUCKPERMS_MAP = {
    "creator": "devolper", "devolper": "devolper", "developer": "devolper",
    "gl.owner": "gowner", "gowner": "gowner",
    "owner": "owner",
    "gl.curator": "gcurator", "gcurator": "gcurator",
    "curator": "curator",
    "z.curator": "zcurator", "zcurator": "zcurator",
    "gl.admin": "gadmin", "gadmin": "gadmin",
    "admin": "admin",
    "gl.moder": "glmoder", "glmoder": "glmoder",
    "st.moder": "stmoder", "stmoder": "stmoder",
    "moder": "moder",
    "ml.moder": "mlmoder", "mlmoder": "mlmoder",
    "helper": "helper", "хелпер": "helper",
    # Project Team — Discord-only, не выдаём Minecraft-группу.
}


class StaffRoleManager:
    """Класс управления ролями персонала в Discord."""

    @staticmethod
    def is_staff_role(role: discord.Role) -> bool:
        """Проверяет, относится ли роль Discord к иерархии персонала."""
        if role.id in STAFF_ROLE_IDS:
            return True
        if role.managed or role.is_default():
            return False
        name = role.name.lower()
        if any(k in name for k in STAFF_ROLE_KEYWORDS):
            return True
        if 22 <= role.position <= 36:
            return True
        return False

    @staticmethod
    def find_staff_role_for_position(guild: discord.Guild, position: str) -> discord.Role | None:
        """Ищет подходящую роль Discord для указанной должности."""
        pos_low = position.strip().lower()
        if pos_low in POSITION_DISCORD_ROLES_MAP:
            role = guild.get_role(POSITION_DISCORD_ROLES_MAP[pos_low])
            if role:
                return role
        for key in sorted(POSITION_DISCORD_ROLES_MAP.keys(), key=len, reverse=True):
            if key in pos_low:
                role = guild.get_role(POSITION_DISCORD_ROLES_MAP[key])
                if role:
                    return role
        for role in guild.roles:
            r_name = role.name.lower()
            if pos_low in r_name and not role.managed and not role.is_default():
                return role
        return None

    @classmethod
    async def strip_discord_staff_roles(
        cls, member: discord.Member, reason: str = "Снятие с должности персонала"
    ) -> tuple[list[str], list[str]]:
        """
        Снимает все роли персонала с участника с учётом иерархии ролей бота.
        Возвращает (список_снятых_ролей, список_заблокированных_иерархией).
        """
        guild = member.guild
        bot_member = guild.me if guild else None
        bot_top_pos = bot_member.top_role.position if bot_member else -1

        staff_roles = [r for r in member.roles if cls.is_staff_role(r)]
        removed_names: list[str] = []
        blocked_names: list[str] = []
        can_remove: list[discord.Role] = []

        for role in staff_roles:
            if bot_member and role.position >= bot_top_pos:
                blocked_names.append(role.name)
                log.warning(
                    f"Role '{role.name}' (pos {role.position}) is >= bot top role. Cannot remove."
                )
            else:
                can_remove.append(role)

        if can_remove:
            try:
                await member.remove_roles(*can_remove, reason=reason)
                removed_names.extend([r.name for r in can_remove])
                log.info(f"Stripped {len(removed_names)} staff roles from {member}: {removed_names}")
            except discord.errors.Forbidden:
                for role in can_remove:
                    try:
                        await member.remove_roles(role, reason=reason)
                        removed_names.append(role.name)
                    except Exception:
                        blocked_names.append(role.name)
            except Exception as e:
                log.warning(f"Error removing roles from {member}: {e}")

        return removed_names, blocked_names

    @classmethod
    async def grant_discord_staff_roles(
        cls,
        member: discord.Member,
        position: str,
        old_position: str = "",
        reason: str = "Назначение в персонал",
    ) -> tuple[list[str], list[str]]:
        """
        Выдаёт базовую роль персонала и роль конкретной должности.
        При повышении/переводе снимает старую роль должности.
        """
        guild = member.guild
        bot_member = guild.me if guild else None
        bot_top_pos = bot_member.top_role.position if bot_member else -1

        granted: list[str] = []
        blocked: list[str] = []
        to_add: list[discord.Role] = []

        # Базовая роль персонала
        base_role = guild.get_role(BASE_STAFF_ROLE_ID)
        if not base_role:
            for r in guild.roles:
                if "сотрудник проекта" in r.name.lower():
                    base_role = r
                    break

        if base_role and base_role not in member.roles:
            if bot_member and base_role.position >= bot_top_pos:
                blocked.append(base_role.name)
            else:
                to_add.append(base_role)

        # Роль должности
        pos_role = cls.find_staff_role_for_position(guild, position)
        if pos_role and pos_role not in member.roles:
            if bot_member and pos_role.position >= bot_top_pos:
                blocked.append(pos_role.name)
            else:
                to_add.append(pos_role)

        # Снятие предыдущей должности при повышении
        if old_position:
            old_role = cls.find_staff_role_for_position(guild, old_position)
            if old_role and old_role in member.roles and old_role != pos_role:
                if bot_member and old_role.position < bot_top_pos:
                    try:
                        await member.remove_roles(old_role, reason=f"Повышение: {old_position} -> {position}")
                        log.info(f"Removed old role '{old_role.name}' from {member}")
                    except Exception as e:
                        log.warning(f"Failed to remove old role '{old_role.name}' from {member}: {e}")

        if to_add:
            try:
                await member.add_roles(*to_add, reason=reason)
                granted.extend([r.name for r in to_add])
                log.info(f"Assigned staff roles {granted} to {member}")
            except discord.errors.Forbidden:
                for r in to_add:
                    try:
                        await member.add_roles(r, reason=reason)
                        granted.append(r.name)
                    except Exception:
                        blocked.append(r.name)
            except Exception as e:
                log.warning(f"Error assigning roles to {member}: {e}")

        return granted, blocked


class MinecraftPermissionsManager:
    """Класс синхронизации прав в Minecraft (LuckPerms) через Pterodactyl API."""

    @staticmethod
    def get_luckperms_group(position: str) -> str:
        """Сопоставляет название должности с группой LuckPerms."""
        pos_low = position.strip().lower()
        if pos_low in POSITION_LUCKPERMS_MAP:
            return POSITION_LUCKPERMS_MAP[pos_low]
        for key in sorted(POSITION_LUCKPERMS_MAP.keys(), key=len, reverse=True):
            if key in pos_low:
                return POSITION_LUCKPERMS_MAP[key]
        clean = re.sub(r"[^\w\.-]", "", pos_low)
        if clean in ALL_LP_GROUPS:
            return clean
        return ""

    @classmethod
    async def grant_minecraft_permissions(cls, nick: str, position: str) -> tuple[bool, str]:
        """Выдаёт игроку группу прав LuckPerms на серверах через Pterodactyl API."""
        group = cls.get_luckperms_group(position)
        if not group:
            log.info("No Minecraft group mapped for position '%s'; skipping LuckPerms grant", position)
            return False, ""

        if not PTERO_API_KEY:
            log.warning("PTERO_API_KEY not configured, skipping Minecraft permissions grant")
            return False, group

        clean_nick = re.sub(r"[^\w]", "", nick)
        if not clean_nick:
            return False, group
        commands = [
            f"lp user {clean_nick} parent set {group}",
            "lp sync",
        ]

        success = False
        headers = {
            "Authorization": f"Bearer {PTERO_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            for srv_id in PTERO_SERVER_IDS:
                try:
                    srv_success = True
                    for cmd in commands:
                        url = f"{PTERO_BASE_URL}/api/client/servers/{srv_id}/command"
                        async with session.post(url, json={"command": cmd}, headers=headers, timeout=5) as resp:
                            if resp.status not in (204, 200):
                                srv_success = False
                    if srv_success:
                        success = True
                        log.info(f"Granted Minecraft group '{group}' to {clean_nick} on server {srv_id}")
                except Exception as e:
                    log.debug(f"Ptero grant command failed on server {srv_id}: {e}")

        return success, group

    @classmethod
    async def revoke_minecraft_permissions(cls, nick: str) -> bool:
        """Сбрасывает группу прав игрока на default в LuckPerms при снятии с должности."""
        if not PTERO_API_KEY:
            log.warning("PTERO_API_KEY not configured, skipping Minecraft permissions revocation")
            return False

        clean_nick = re.sub(r"[^\w]", "", nick)
        if not clean_nick:
            return False

        commands = [
            f"lp user {clean_nick} parent set default",
            f"lp user {clean_nick} clear",
            "lp sync",
        ]

        success = False
        headers = {
            "Authorization": f"Bearer {PTERO_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            for srv_id in PTERO_SERVER_IDS:
                try:
                    srv_success = True
                    for cmd in commands:
                        url = f"{PTERO_BASE_URL}/api/client/servers/{srv_id}/command"
                        async with session.post(url, json={"command": cmd}, headers=headers, timeout=5) as resp:
                            if resp.status not in (204, 200):
                                srv_success = False
                    if srv_success:
                        success = True
                        log.info(f"Revoked Minecraft permissions for {clean_nick} on server {srv_id}")
                except Exception as e:
                    log.debug(f"Ptero command failed on server {srv_id}: {e}")

        return success

