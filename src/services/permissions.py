"""
Discord roles management and Minecraft LuckPerms permission synchronization via Pterodactyl.
"""

import re
import asyncio
import json
import aiohttp
import discord
from src.core.config import (
    log, PTERO_API_KEY, PTERO_BASE_URL, PTERO_SERVER_IDS, ROLE_IDS
)

# Роли персонала WeriGrief. Project Team — только Discord, без группы LuckPerms.
ROLE_CREATOR = ROLE_IDS["creator"]
ROLE_GL_OWNER = ROLE_IDS["gl_owner"]
ROLE_OWNER = ROLE_IDS["owner"]
ROLE_GL_CURATOR = ROLE_IDS["gl_curator"]
ROLE_CURATOR = ROLE_IDS["curator"]
ROLE_Z_CURATOR = ROLE_IDS["z_curator"]
ROLE_GL_ADMIN = ROLE_IDS["gl_admin"]
ROLE_ADMIN = ROLE_IDS["admin"]
ROLE_GL_MODER = ROLE_IDS["gl_moder"]
ROLE_ST_MODER = ROLE_IDS["st_moder"]
ROLE_MODER = ROLE_IDS["moder"]
ROLE_ML_MODER = ROLE_IDS["ml_moder"]
ROLE_ML_ADMIN = ROLE_IDS["ml_admin"]
ROLE_HELPER = ROLE_IDS["helper"]
ROLE_PROJECT_TEAM = ROLE_IDS["project_team"]

STAFF_ROLE_IDS = {
    ROLE_CREATOR, ROLE_GL_OWNER, ROLE_OWNER, ROLE_GL_CURATOR, ROLE_CURATOR,
    ROLE_Z_CURATOR, ROLE_GL_ADMIN, ROLE_ADMIN, ROLE_GL_MODER, ROLE_ST_MODER,
    ROLE_MODER, ROLE_ML_MODER, ROLE_ML_ADMIN, ROLE_HELPER, ROLE_PROJECT_TEAM,
}

STAFF_ROLE_KEYWORDS = (
    "creator", "gl.owner", "owner", "curator", "gadmin", "admin",
    "moder", "helper", "project team", "персонал", "руководство",
)

# Отдельной базовой роли персонала в новой системе нет.
BASE_STAFF_ROLE_ID = ROLE_PROJECT_TEAM

POSITION_DISCORD_ROLES_MAP = {
    "creator": ROLE_CREATOR, "devolper": ROLE_CREATOR, "developer": ROLE_CREATOR,
    "gl.owner": ROLE_GL_OWNER, "gl owner": ROLE_GL_OWNER, "gowner": ROLE_GL_OWNER,
    "owner": ROLE_OWNER,
    "gl.curator": ROLE_GL_CURATOR, "gl curator": ROLE_GL_CURATOR, "gcurator": ROLE_GL_CURATOR, "g.curator": ROLE_GL_CURATOR, "g curator": ROLE_GL_CURATOR,
    "curator": ROLE_CURATOR,
    "z.curator": ROLE_Z_CURATOR, "z curator": ROLE_Z_CURATOR, "zcurator": ROLE_Z_CURATOR,
    "gl.admin": ROLE_GL_ADMIN, "gl admin": ROLE_GL_ADMIN, "gadmin": ROLE_GL_ADMIN,
    "admin": ROLE_ADMIN,
    "ml.admin": ROLE_ML_ADMIN, "ml admin": ROLE_ML_ADMIN, "mladmin": ROLE_ML_ADMIN,
    "gl.moder": ROLE_GL_MODER, "gl moder": ROLE_GL_MODER, "glmoder": ROLE_GL_MODER,
    "st.moder": ROLE_ST_MODER, "st moder": ROLE_ST_MODER, "stmoder": ROLE_ST_MODER,
    "moder": ROLE_MODER,
    "ml.moder": ROLE_ML_MODER, "ml moder": ROLE_ML_MODER, "mlmoder": ROLE_ML_MODER,
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
    "разработчик": "devolper", "гл.разработчик": "devolper",
    "gl.owner": "gowner", "gl owner": "gowner", "gowner": "gowner",
    "owner": "owner",
    "gl.curator": "gcurator", "gl curator": "gcurator", "gcurator": "gcurator", "g.curator": "gcurator", "g curator": "gcurator",
    "curator": "curator",
    "z.curator": "zcurator", "z curator": "zcurator", "zcurator": "zcurator",
    "gl.admin": "gadmin", "gl admin": "gadmin", "gadmin": "gadmin",
    "admin": "admin",
    "gl.moder": "glmoder", "gl moder": "glmoder", "glmoder": "glmoder",
    "st.moder": "stmoder", "st moder": "stmoder", "stmoder": "stmoder",
    "moder": "moder",
    "ml.moder": "mlmoder", "ml moder": "mlmoder", "mlmoder": "mlmoder", "ml admin": "mladmin", "mladmin": "mladmin",
    "helper": "helper", "хелпер": "helper",
    # Project Team — Discord-only, не выдаём Minecraft-группу.
}




# Порядок должностей для /promote и /demote — от младшей к старшей.
# ML ADMIN имеет отдельную Discord-роль WeriGrief.
POSITION_ORDER = [
    "HELPER", "D.HELPER", "ML.MODER", "MODER", "ST.MODER", "GL.MODER", "D.MODER",
    "ML.ADMIN", "ADMIN", "GL.ADMIN", "D.ADMIN", "Z.CURATOR", "CURATOR",
    "GL.CURATOR", "OWNER", "D.OWNER", "GL.OWNER", "CREATOR",
    "Разработчик", "Гл.Разработчик", "PR Manager",
    "Сотрудник проекта", "На испытании", "Кандидат",
]

class StaffRoleManager:
    """Класс управления ролями персонала в Discord."""

    @staticmethod
    def is_staff_role(role: discord.Role) -> bool:
        """Проверяет, относится ли роль Discord к иерархии персонала."""
        # Только точные ID ролей WeriGrief. Не трогаем донатные и прочие роли.
        if role.id in STAFF_ROLE_IDS:
            return True
        normalized = role.name.casefold()
        return normalized in {
            "creator", "gl.owner", "owner", "d.owner", "gl.curator", "curator",
            "z.curator", "gl.admin", "admin", "ml.admin", "d.admin", "gl.moder",
            "st.moder", "moder", "ml.moder", "d.moder", "helper", "d.helper",
            "гл.разработчик", "разработчик", "pr manager", "сотрудник проекта",
            "на испытании", "кандидат",
        }

    @staticmethod
    def find_staff_role_for_position(guild: discord.Guild, position: str) -> discord.Role | None:
        """Ищет роль сначала по реальному ID WeriGrief, затем по точному имени роли."""
        pos = position.strip()
        pos_low = pos.casefold()
        role_id = POSITION_DISCORD_ROLES_MAP.get(pos_low)
        if role_id:
            role = guild.get_role(role_id)
            if role:
                return role
        # Некоторые должности присутствуют в требованиях, но в исходниках архива
        # для них нет ID. Не выдумываем ID — ищем точное имя на самом сервере.
        exact_names = {
            "gl.owner": ("GL.OWNER",), "d.owner": ("D.OWNER",),
            "gl.curator": ("GL.CURATOR",), "z.curator": ("Z.CURATOR",),
            "gl.admin": ("GL.ADMIN",), "ml.admin": ("ML.ADMIN",), "d.admin": ("D.ADMIN",),
            "gl.moder": ("GL.MODER",), "st.moder": ("ST.MODER",),
            "ml.moder": ("ML.MODER",), "d.moder": ("D.MODER",),
            "d.helper": ("D.HELPER",), "helper": ("HELPER",),
            "creator": ("CREATOR",), "developer": ("Разработчик",),
            "главный разработчик": ("Гл.Разработчик",),
            "pr manager": ("PR Manager",), "сотрудник проекта": ("Сотрудник проекта",),
            "на испытании": ("На испытании", "⚠️ На испытании"),
            "кандидат": ("Кандидат",),
        }
        candidates = exact_names.get(pos_low, (pos,))
        for candidate in candidates:
            role = discord.utils.get(guild.roles, name=candidate)
            if role:
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
        if base_role and base_role not in member.roles:
            if bot_member and base_role.position >= bot_top_pos:
                blocked.append(base_role.name)
            else:
                to_add.append(base_role)
        elif base_role is None:
            blocked.append(f"Не найдена базовая роль с ID {BASE_STAFF_ROLE_ID}")

        # Роль должности
        pos_role = cls.find_staff_role_for_position(guild, position)
        if pos_role is None:
            blocked.append(f"Discord-роль не настроена: {position}")
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
                        await member.remove_roles(old_role, reason=f"Изменение должности: {old_position} -> {position}")
                        log.info(f"Removed old role '{old_role.name}' from {member}")
                    except Exception as e:
                        blocked.append(old_role.name)
                        log.warning(f"Failed to remove old role '{old_role.name}' from {member}: {e}")
                else:
                    blocked.append(old_role.name)

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
    """Синхронизация Minecraft через Pterodactyl Client WebSocket API."""

    @staticmethod
    async def _send_console_commands(server_id: str, commands: list[str]) -> bool:
        """Authenticate through Pterodactyl Client API and send commands to server console.

        PTERO_API_KEY must be a CLIENT key (usually starts with ptlc_), not an
        Application key (ptla_). We keep the socket open briefly so console output
        and explicit command errors can be collected instead of claiming success
        immediately after writing to the websocket.
        """
        from src.core.config import PTERO_API_KEY, PTERO_BASE_URL
        if not PTERO_API_KEY:
            log.error("Minecraft command not sent: PTERO_API_KEY is empty")
            return False
        if PTERO_API_KEY.startswith("ptla_"):
            log.error("Minecraft command not sent: PTERO_API_KEY is an Application API key (ptla_). A Client API key (ptlc_) is required for /api/client/servers/.../websocket.")
            return False
        headers = {
            "Authorization": f"Bearer {PTERO_API_KEY}",
            "Accept": "application/json",
        }
        endpoint = f"{PTERO_BASE_URL.rstrip('/')}/api/client/servers/{server_id}/websocket"
        try:
            timeout = aiohttp.ClientTimeout(total=25)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(endpoint, headers=headers) as response:
                    if response.status != 200:
                        body = await response.text()
                        log.error("Pterodactyl websocket-token request failed: server=%s status=%s body=%s", server_id, response.status, body[:500])
                        return False
                    payload = await response.json(content_type=None)
                data = payload.get("data", {})
                socket_url = data.get("socket")
                token = data.get("token")
                if not socket_url or not token:
                    log.error("Pterodactyl response missing socket/token for server %s: %s", server_id, str(payload)[:500])
                    return False
                async with session.ws_connect(socket_url, heartbeat=20) as ws:
                    await ws.send_json({"event": "auth", "args": [token]})
                    authenticated = False
                    deadline = asyncio.get_running_loop().time() + 8
                    while asyncio.get_running_loop().time() < deadline:
                        try:
                            msg = await ws.receive(timeout=1.5)
                        except asyncio.TimeoutError:
                            continue
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                event = json.loads(msg.data)
                            except (TypeError, ValueError):
                                continue
                            if event.get("event") == "auth success":
                                authenticated = True
                                break
                            if event.get("event") in {"jwt error", "token expired"}:
                                log.error("Pterodactyl console auth rejected for %s: %s", server_id, event)
                                return False
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                    if not authenticated:
                        log.error("Pterodactyl console authentication timed out for server %s", server_id)
                        return False

                    for command in commands:
                        await ws.send_json({"event": "send command", "args": [command]})
                        log.info("Minecraft console command queued on %s: %s", server_id, command[:180])
                        await asyncio.sleep(0.25)

                    # Read console output briefly. Pterodactyl does not provide a
                    # per-command acknowledgement, but this catches obvious errors.
                    output_lines = []
                    idle_deadline = asyncio.get_running_loop().time() + 2.5
                    while asyncio.get_running_loop().time() < idle_deadline:
                        try:
                            msg = await ws.receive(timeout=0.6)
                        except asyncio.TimeoutError:
                            continue
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                event = json.loads(msg.data)
                            except (TypeError, ValueError):
                                continue
                            if event.get("event") == "console output":
                                args = event.get("args") or []
                                if args:
                                    output_lines.append(str(args[0])[:500])
                            elif event.get("event") in {"jwt error", "token expired"}:
                                log.error("Pterodactyl websocket token expired during command on %s", server_id)
                                return False
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                    if output_lines:
                        joined = " | ".join(output_lines[-8:])
                        log.info("Minecraft console output from %s: %s", server_id, joined[:2000])
                        if any(term in joined.casefold() for term in ("unknown command", "неизвестная команда", "not found", "no player was found", "player not found")):
                            log.error("Minecraft rejected command on %s: %s", server_id, joined[:1000])
                            return False
                    return True
        except Exception as exc:
            log.exception("Pterodactyl console websocket failed for server %s: %s", server_id, exc)
            return False

    @staticmethod
    def get_luckperms_group(position: str) -> str:
        """Сопоставляет должность с группой LuckPerms; LP_GROUP_MAP из .env имеет приоритет."""
        import os
        pos_low = position.strip().casefold()
        try:
            custom = json.loads(os.getenv("LP_GROUP_MAP", "{}") or "{}")
            if isinstance(custom, dict):
                normalized = {str(k).strip().casefold(): str(v).strip() for k, v in custom.items()}
                if normalized.get(pos_low):
                    return normalized[pos_low]
        except (TypeError, ValueError):
            log.error("LP_GROUP_MAP must be valid JSON object, e.g. {\"D.ADMIN\":\"admin\"}")
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
        """Выдаёт игроку группу LuckPerms через консоль Pterodactyl WebSocket."""
        from src.core.config import PTERO_SERVER_IDS
        group = cls.get_luckperms_group(position)
        if not group:
            log.info("No Minecraft group mapped for position '%s'; skipping LuckPerms grant", position)
            return False, ""
        clean_nick = re.sub(r"[^A-Za-z0-9_]", "", str(nick or ""))
        if not clean_nick:
            return False, group
        commands = [f"lp user {clean_nick} parent set {group}", "lp sync"]
        success = False
        for server_id in PTERO_SERVER_IDS:
            if await cls._send_console_commands(server_id, commands):
                success = True
                log.info("LuckPerms grant command sent for %s on server %s", clean_nick, server_id)
        return success, group

    @classmethod
    async def revoke_minecraft_permissions(cls, nick: str) -> bool:
        """Сбрасывает группу LuckPerms игрока на default через консоль."""
        from src.core.config import PTERO_SERVER_IDS
        clean_nick = re.sub(r"[^A-Za-z0-9_]", "", str(nick or ""))
        if not clean_nick:
            return False
        # Не используем `lp user clear`: он удаляет все персональные permission nodes.
        # Сбрасываем только родительскую группу, сохраняя индивидуальные права игрока.
        commands = [
            f"lp user {clean_nick} parent set default",
            "lp sync",
        ]
        success = False
        for server_id in PTERO_SERVER_IDS:
            if await MinecraftPermissionsManager._send_console_commands(server_id, commands):
                success = True
                log.info("LuckPerms revoke commands sent for %s on server %s", clean_nick, server_id)
        return success

    @staticmethod
    async def send_private_minecraft_warning(nick: str, reason: str, total_warns: int, source: str = "Персонал") -> bool:
        """Отправляет tellraw только конкретному игроку; общий чат не затрагивается."""
        import json
        from src.core.config import PTERO_SERVER_IDS
        clean_nick = re.sub(r"[^A-Za-z0-9_]", "", str(nick or ""))
        if not clean_nick:
            log.warning("Minecraft private warning skipped: invalid nickname")
            return False
        safe_reason = str(reason or "Без указания причины")[:300]
        safe_source = str(source or "Персонал")[:60]
        message = (
            f"[WeriGrief] Вам выдано предупреждение: {safe_reason}. "
            f"Всего предупреждений: {int(total_warns)}. Раздел: {safe_source}."
        )
        tellraw = "tellraw " + clean_nick + " " + json.dumps(
            {"text": message, "color": "red"}, ensure_ascii=False, separators=(",", ":")
        )
        delivered = False
        for server_id in PTERO_SERVER_IDS:
            if await MinecraftPermissionsManager._send_console_commands(server_id, [tellraw]):
                delivered = True
        return delivered

