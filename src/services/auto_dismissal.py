"""Автоматическое снятие сотрудника после третьего предупреждения."""

import asyncio
import discord

from src.core.config import log, GUILD_ID, STAFF_NOTIFY_CHANNEL
from src.services.permissions import StaffRoleManager, MinecraftPermissionsManager
from src.utils.embeds import EmbedBuilder


_locks: dict[str, asyncio.Lock] = {}


async def auto_dismiss_staff(
    bot, sheets_monitor, staff_resolver, guild, nick: str,
    reason: str = "Автоматическое снятие: получено 3 предупреждения",
) -> bool:
    if not sheets_monitor or not nick:
        return False

    key = nick.strip().replace("_", "").casefold()
    lock = _locks.setdefault(key, asyncio.Lock())

    async with lock:
        guild = guild or bot.get_guild(GUILD_ID)
        member = None

        if guild and staff_resolver:
            try:
                member = staff_resolver.find_member_by_mc_nick(guild, nick)
            except Exception:
                log.exception("Cannot find Discord member for %s", nick)

        removed_info = sheets_monitor.remove_staff_member(
            nick,
            removed_by="Автоматическая система WeriTime",
            reason=reason,
        )

        if not removed_info:
            return False

        removed_roles, blocked_roles = [], []
        roles_ok = member is None

        if member:
            try:
                removed_roles, blocked_roles = await StaffRoleManager.strip_discord_staff_roles(
                    member,
                    reason="Автоматическое снятие после третьего предупреждения",
                )
                roles_ok = not blocked_roles
            except Exception:
                log.exception("Failed to remove Discord roles for %s", nick)

        minecraft_ok = False
        try:
            minecraft_ok = await MinecraftPermissionsManager.revoke_minecraft_permissions(nick)
        except Exception:
            log.exception("Failed to revoke Minecraft groups for %s", nick)

        role_status = (
            f"Сняты: {', '.join(removed_roles) or 'нет'}. "
            f"Не удалось снять из-за иерархии: {', '.join(blocked_roles)}."
            if blocked_roles
            else f"Сняты: {', '.join(removed_roles) or 'не обнаружены'}."
        )

        minecraft_status = (
            "Команды снятия должностных групп отправлены; применение требует проверки."
            if minecraft_ok
            else "Не удалось подтвердить отправку команд. Требуется проверка."
        )

        dm_sent = False
        if member:
            try:
                dm_sent = await EmbedBuilder.send_dismissal_dm(
                    member,
                    nick=nick,
                    position=removed_info.get("position", "—"),
                    removed_by="Автоматическая система WeriTime",
                    reason=reason,
                    warnings_history=removed_info.get("warnings_history", []),
                    automatic=True,
                    role_status=role_status,
                    minecraft_status=minecraft_status,
                )
            except Exception:
                log.exception("Failed to send dismissal DM to %s", nick)

        channel = bot.get_channel(STAFF_NOTIFY_CHANNEL) if STAFF_NOTIFY_CHANNEL else None

        if channel is None and STAFF_NOTIFY_CHANNEL:
            try:
                channel = await bot.fetch_channel(STAFF_NOTIFY_CHANNEL)
            except Exception:
                log.exception("Cannot access staff notification channel")

        if channel:
            try:
                embed = EmbedBuilder.build_staff_removed_embed(
                    nick=nick,
                    position=removed_info.get("position", "—"),
                    email=removed_info.get("email", ""),
                    warns_cleared=removed_info.get("warns_cleared", 0),
                    removed_by="Автоматическая система WeriTime",
                    reason=reason,
                )
                embed.add_field(
                    name="📚 Архив предупреждений",
                    value=str(len(removed_info.get("warnings_history", []))),
                    inline=True,
                )
                embed.add_field(
                    name="🎭 Discord-роли",
                    value=role_status[:1024],
                    inline=False,
                )
                embed.add_field(
                    name="⛏️ LuckPerms",
                    value=minecraft_status,
                    inline=False,
                )
                embed.add_field(
                    name="✉️ Личное уведомление",
                    value="Отправлено" if dm_sent else "Не доставлено или Discord-пользователь не найден",
                    inline=False,
                )
                await channel.send(embed=embed)
            except Exception:
                log.exception("Failed to send auto-dismissal notification")

        log.info(
            "AUTO DISMISSAL %s: history=%s, roles=%s, blocked=%s, roles_ok=%s, minecraft=%s, dm=%s",
            nick,
            len(removed_info.get("warnings_history", [])),
            removed_roles,
            blocked_roles,
            roles_ok,
            minecraft_ok,
            dm_sent,
        )

        return True
