"""
Discord roles management and Minecraft LuckPerms permission synchronization via Pterodactyl.
"""

import re
import asyncio
import json
import aiohttp
import discord
from src.core.config import (
    log, PTERO_API_KEY, PTERO_BASE_URL, PTERO_SERVER_IDS, ROLE_IDS,
    STAFF_ROLE_SPECS,
)

# Роли персонала WeriTime. Project Team — базовая Discord-роль без группы LuckPerms.
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
    "creator": ROLE_OWNER, "devolper": ROLE_OWNER, "developer": ROLE_OWNER,
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

# Точные группы LuckPerms, названия которых пользователь подтвердил для Minecraft.
ALL_LP_GROUPS = {
    "owner", "zamowner", "techadmin", "admin", "gladmin", "mladmin",
    "kyrva", "zamkyrva", "glmoder", "moder", "mlmoder",
}

POSITION_LUCKPERMS_MAP = {
    # Старые должности разработчика используют Minecraft-группу techadmin.
    "creator": "techadmin",
    "developer": "techadmin",
    "devolper": "techadmin",
    "разработчик": "techadmin",
    "гл.разработчик": "techadmin",

    "gl.owner": "zamowner",
    "gl owner": "zamowner",
    "gowner": "zamowner",
    "glowner": "zamowner",
    "со-владелец": "zamowner",
    "owner": "owner",
    "владелец": "owner",

    # В Minecraft нет отдельной группы для Гл.Куратора.
    "curator": "kyrva",
    "куратор": "kyrva",
    "z.curator": "zamkyrva",
    "z curator": "zamkyrva",
    "zcurator": "zamkyrva",
    "зам.куратор": "zamkyrva",
    "зам куратор": "zamkyrva",

    "gl.admin": "gladmin",
    "gl admin": "gladmin",
    "gadmin": "gladmin",
    "гл.админ": "gladmin",
    "admin": "admin",
    "админ": "admin",
    "ml.admin": "mladmin",
    "ml admin": "mladmin",
    "mladmin": "mladmin",
    "мл.админ": "mladmin",

    "gl.moder": "glmoder",
    "gl moder": "glmoder",
    "glmoder": "glmoder",
    "гл.модер": "glmoder",
    "moder": "moder",
    "модер": "moder",
    "ml.moder": "mlmoder",
    "ml moder": "mlmoder",
    "mlmoder": "mlmoder",
    "мл.модер": "mlmoder",
}




# Порядок для повышения и понижения — от младшей должности к старшей.
POSITION_ORDER = [
    "Сотрудник проекта", "HELPER", "ML.MODER", "MODER", "GL.MODER",
    "ML.ADMIN", "ADMIN", "GL.ADMIN", "Z.CURATOR", "CURATOR",
    "GL.CURATOR", "GL.OWNER", "OWNER",
]

POSITION_CHOICES = [
    ("Сотрудник проекта", "Сотрудник проекта"),
    ("Стажер", "HELPER"),
    ("Мл.Модер", "ML.MODER"),
    ("Модер", "MODER"),
    ("Гл.Модер", "GL.MODER"),
    ("Мл.Админ", "ML.ADMIN"),
    ("Админ", "ADMIN"),
    ("Гл.Админ", "GL.ADMIN"),
    ("Зам.Куратор", "Z.CURATOR"),
    ("Куратор", "CURATOR"),
    ("Гл.Куратор", "GL.CURATOR"),
    ("Со-Владелец", "GL.OWNER"),
    ("Владелец", "OWNER"),
]

POSITION_RANK_ALIASES = {
    "CREATOR": "OWNER", "DEVELOPER": "OWNER", "D.OWNER": "GL.OWNER",
    "GCURATOR": "GL.CURATOR", "G.CURATOR": "GL.CURATOR",
    "D.ADMIN": "ADMIN", "D.MODER": "MODER", "ST.MODER": "MODER",
    "D.HELPER": "HELPER", "PR MANAGER": "Сотрудник проекта",
    "НА ИСПЫТАНИИ": "HELPER", "КАНДИДАТ": "Сотрудник проекта",
}


def normalize_position_for_order(position: str) -> str:
    normalized = position.strip().upper()
    return POSITION_RANK_ALIASES.get(normalized, normalized)

class StaffRoleManager:
    """Класс управления ролями персонала в Discord."""

    @staticmethod
    async def ensure_staff_roles(guild: discord.Guild) -> None:
        """Create missing WeriTime roles and keep their names, colors and order in sync."""
        old_names = {
            "owner": ("OWNER", "Владелец"), "gl_owner": ("GL.OWNER", "D.OWNER", "Со-Владелец"),
            "gl_curator": ("GL.CURATOR", "Гл.Куратор"), "curator": ("CURATOR", "Куратор"),
            "z_curator": ("Z.CURATOR", "Зам.Куратор"), "gl_admin": ("GL.ADMIN", "Гл.Админ"),
            "admin": ("ADMIN", "Админ"), "ml_admin": ("ML.ADMIN", "Мл.Админ"),
            "gl_moder": ("GL.MODER", "Гл.Модер"), "moder": ("MODER", "Модер"),
            "ml_moder": ("ML.MODER", "Мл.Модер"), "helper": ("HELPER", "D.HELPER", "Стажер"),
            "project_team": ("PROJECT TEAM", "Сотрудник проекта"),
        }
        presets = {
            "basic": {
                "view_channel": True,
                "send_messages": True,
                "read_message_history": True,
                "add_reactions": True,
                "attach_files": True,
                "embed_links": True,
                "connect": True,
                "speak": True,
                "use_application_commands": True,
            },
            "moderator": {
                "moderate_members": True,
                "manage_messages": True,
            },
            "admin": {
                "kick_members": True,
                "ban_members": True,
                "moderate_members": True,
                "manage_messages": True,
                "view_audit_log": True,
                "manage_nicknames": True,
            },
            "lead": {
                "manage_roles": True,
                "manage_channels": True,
                "kick_members": True,
                "ban_members": True,
                "moderate_members": True,
                "manage_messages": True,
                "view_audit_log": True,
                "manage_nicknames": True,
            },
            "owner": {
                "manage_guild": True,
                "manage_roles": True,
                "manage_channels": True,
                "kick_members": True,
                "ban_members": True,
                "moderate_members": True,
                "manage_messages": True,
                "view_audit_log": True,
                "manage_nicknames": True,
                "manage_threads": True,
            },
        }
        roles_in_order = []
        for spec in STAFF_ROLE_SPECS:
            role = guild.get_role(ROLE_IDS[spec["key"]])
            if role is None:
                known_names = {*old_names[spec["key"]], spec["name"]}
                role = next(
                    (r for r in guild.roles
                     if r.name.casefold() in {n.casefold() for n in known_names}),
                    None,
                )
            if spec["permissions"] == "owner":
                permissions = discord.Permissions.all()
                permissions.administrator = False
            else:
                permissions = discord.Permissions(**presets[spec["permissions"]])
            try:
                if role is None:
                    role = await guild.create_role(
                        name=spec["name"],
                        colour=discord.Colour(spec["color"]),
                        permissions=permissions,
                        hoist=True,
                        mentionable=False,
                        reason="Создание и настройка ролей WeriTime",
                    )
                    log.info("Created WeriTime role %s", spec["name"])
                else:
                    role = await role.edit(
                        name=spec["name"],
                        colour=discord.Colour(spec["color"]),
                        permissions=permissions,
                        hoist=True,
                        mentionable=False,
                        reason="Синхронизация ролей WeriTime",
                    )
                roles_in_order.append(role)
            except discord.HTTPException:
                log.exception("Could not create or configure role %s", spec["name"])
                if role is not None:
                    roles_in_order.append(role)

        bot_member = guild.me
        if bot_member and roles_in_order:
            top_position = min(bot_member.top_role.position - 1, len(guild.roles) - 1)
            if top_position >= len(roles_in_order):
                positions = {
                    role: top_position - index
                    for index, role in enumerate(roles_in_order)
                }
                try:
                    await guild.edit_role_positions(positions=positions)
                except discord.HTTPException:
                    log.exception("Could not arrange WeriTime roles; check the bot role position")
            else:
                log.warning(
                    "Move the bot's highest role above the 13 WeriTime staff roles to set their order"
                )

    @staticmethod
    def is_staff_role(role: discord.Role) -> bool:
        """Проверяет, относится ли роль Discord к иерархии персонала."""
        # Только точные ID ролей WeriTime. Не трогаем донатные и прочие роли.
        if role.id in STAFF_ROLE_IDS:
            return True
        normalized = role.name.casefold()
        return normalized in {
            "creator", "gl.owner", "owner", "d.owner", "gl.curator", "curator",
            "z.curator", "gl.admin", "admin", "ml.admin", "d.admin", "gl.moder",
            "st.moder", "moder", "ml.moder", "d.moder", "helper", "d.helper",
            "d.owner",
            "гл.разработчик", "разработчик", "pr manager", "сотрудник проекта",
            "владелец", "со-владелец", "гл.куратор", "куратор", "зам.куратор",
            "гл.админ", "админ", "мл.админ", "гл.модер", "модер", "мл.модер",
            "стажер",
            "на испытании", "кандидат",
        }

    @staticmethod
    def find_staff_role_for_position(guild: discord.Guild, position: str) -> discord.Role | None:
        """Ищет роль сначала по реальному ID WeriTime, затем по точному имени роли."""
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
            "gl.owner": ("💎・Со-Владелец", "Со-Владелец"), "d.owner": ("💎・Со-Владелец", "Со-Владелец"),
            "gl.curator": ("🌟・Гл.Куратор", "Гл.Куратор"), "z.curator": ("✨・Зам.Куратор", "Зам.Куратор"),
            "gl.admin": ("🛡️・Гл.Админ", "Гл.Админ"), "ml.admin": ("🔰・Мл.Админ", "Мл.Админ"),
            "d.admin": ("⚔️・Админ", "Админ"), "gl.moder": ("🚨・Гл.Модер", "Гл.Модер"),
            "st.moder": ("🔨・Модер", "Модер"), "ml.moder": ("🧹・Мл.Модер", "Мл.Модер"),
            "d.moder": ("🔨・Модер", "Модер"), "d.helper": ("🤝・Стажер", "Стажер"),
            "helper": ("🤝・Стажер", "Стажер"),
            "creator": ("CREATOR",), "developer": ("Разработчик",),
            "главный разработчик": ("Гл.Разработчик",),
            "pr manager": ("PR Manager",), "сотрудник проекта": ("Сотрудник проекта",),
            "стажер": ("🤝・Стажер", "Стажер"), "owner": ("👑・Владелец", "Владелец"),
            "curator": ("🔮・Куратор", "Куратор"), "admin": ("⚔️・Админ", "Админ"),
            "moder": ("🔨・Модер", "Модер"),
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
        base_role = guild.get_role(BASE_STAFF_ROLE_ID) or discord.utils.get(
            guild.roles, name="Сотрудник проекта"
        )
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
        if pos_role and pos_role not in member.roles and pos_role not in to_add:
            if bot_member and pos_role.position >= bot_top_pos:
                blocked.append(pos_role.name)
            else:
                to_add.append(pos_role)

        # Сверяем весь набор управляемых ролей с текущей должностью из таблицы.
        # Базовая роль остаётся, а лишние должностные роли снимаются.
        keep_ids = {r.id for r in (base_role, pos_role) if r is not None}
        stale_roles = [
            role for role in member.roles
            if role.id in STAFF_ROLE_IDS and role.id not in keep_ids
        ]
        for old_role in stale_roles:
            if bot_member and old_role.position < bot_top_pos:
                try:
                    await member.remove_roles(
                        old_role,
                        reason=reason if not old_position else f"Изменение должности: {old_position} -> {position}",
                    )
                    log.info("Removed stale staff role '%s' from %s", old_role.name, member)
                except Exception as e:
                    blocked.append(old_role.name)
                    log.warning("Failed to remove stale staff role '%s' from %s: %s", old_role.name, member, e)
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
                async with session.ws_connect(
                socket_url,
                heartbeat=20,
                headers={"Origin": PTERO_BASE_URL.rstrip("/")}
            ) as ws:
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
                        if any(term in joined.casefold() for term in ("unknown command", "неизвестная команда", "not found", "no player was found", "player not found", "a user for", "could not be found", "не найден")):
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
        if pos_low in {"gl.curator", "gl curator", "gcurator", "гл.куратор", "гл куратор"}:
            return ""
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
        """Снимает должностные группы LuckPerms на всех настроенных серверах."""
        from src.core.config import PTERO_SERVER_IDS

        clean_nick = re.sub(r"[^A-Za-z0-9_]", "", str(nick or ""))
        if not clean_nick or not PTERO_SERVER_IDS:
            return False

        commands = [
            f"lp user {clean_nick} parent remove {group}"
            for group in sorted(ALL_LP_GROUPS)
        ]
        commands.append("lp sync")

        results = []
        for server_id in PTERO_SERVER_IDS:
            try:
                server_ok = await cls._send_console_commands(server_id, commands)
            except Exception:
                log.exception(
                    "LuckPerms revoke failed for %s on server %s",
                    clean_nick,
                    server_id,
                )
                server_ok = False

            results.append(bool(server_ok))
            log.info(
                "LuckPerms revoke for %s on server %s: %s",
                clean_nick,
                server_id,
                "commands sent" if server_ok else "FAILED",
            )

        return bool(results) and all(results)

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
            f"[WeriTime] Вам выдано предупреждение: {safe_reason}. "
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

