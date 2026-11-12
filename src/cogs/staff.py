"""
Cog for staff roster management (/staff, /dismissed, /appoint, /unstaff).
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
from src.core.config import GUILD_ID, STAFF_NOTIFY_CHANNEL, LEADERSHIP_IDS
from src.utils.embeds import EmbedBuilder
from src.services.permissions import StaffRoleManager, MinecraftPermissionsManager


class StaffCog(commands.Cog, name="Staff"):
    """Команды управления составом персонала и кадровым реестром."""

    def __init__(self, bot, sheets_monitor, staff_resolver):
        self.bot = bot
        self.sheets_monitor = sheets_monitor
        self.staff_resolver = staff_resolver

    @commands.hybrid_command(
        name="staff",
        description="Показать текущий состав администрации сервера",
    )
    async def cmd_staff(self, ctx: commands.Context):
        """Отображает список персонала из Google Таблицы."""
        await ctx.defer()
        if not self.sheets_monitor:
            await ctx.send("❌ Система Google Sheets еще инициализируется.")
            return

        author_staff = self.staff_resolver.get_staff_entry_for_member(ctx.author)
        if not author_staff and ctx.author.id not in LEADERSHIP_IDS:
            await ctx.send(
                "❌ **Доступ запрещен:** вы не числитесь в официальной таблице персонала (Google Sheets).\n"
                "Наличие роли или прав в Discord без записи в таблице не дает права на использование команд персонала.",
                ephemeral=True,
            )
            return

        try:
            staff = self.sheets_monitor.get_staff_list()
        except Exception as e:
            await ctx.send(f"❌ Ошибка: {e}")
            return

        embed = discord.Embed(
            title="🛡️ Текущий состав администрации WeriGrief",
            color=0x5865F2,
            description="Актуальный список персонала из Google Таблицы.",
            timestamp=datetime.now(timezone.utc),
        )

        current_section = ""
        for s in staff:
            if s.get("section"):
                current_section = s["section"]
                embed.add_field(name=f"\n━━━ {current_section} ━━━", value="\u200b", inline=False)
            elif s.get("nick"):
                val = f"**{s.get('position', '—')}**"
                if s.get("discord"):
                    val += f"\n💬 `{s['discord'].strip()}`"
                if s.get("hours") and s["hours"] != "0":
                    val += f"\n⏱ {s['hours']} ч."
                warn_val = s.get("warns", "0")
                if warn_val and warn_val != "0":
                    val += f"\n⚠️ Варнов: **{warn_val}**"
                embed.add_field(name=f"`{s['nick']}`", value=val, inline=True)

        embed.set_footer(text="WeriGrief Staff System • Префикс: !staff / Слэш: /staff")
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="dismissed",
        description="Показать журнал учета снятых сотрудников (для руководства)",
    )
    async def cmd_dismissed(self, ctx: commands.Context):
        """Отображает архив уволенного персонала."""
        await ctx.defer()
        guild = ctx.guild or self.bot.get_guild(GUILD_ID)
        if not self.staff_resolver.is_leadership(ctx.author, guild):
            await ctx.send("❌ Команда доступна только **Владельцам**, **Со-владельцам** и **Кураторам** проекта.", ephemeral=True)
            return

        if not self.sheets_monitor:
            await ctx.send("❌ Система Google Sheets еще инициализируется.")
            return

        history = self.sheets_monitor.dismissed_history
        if not history:
            await ctx.send("ℹ️ Журнал учета снятого персонала пока пуст.")
            return

        embed = discord.Embed(
            title="📁 Журнал учета снятого персонала («Админ пока»)",
            color=0x95A5A6,
            description=f"Всего архивных записей: **{len(history)}**",
            timestamp=datetime.now(timezone.utc),
        )

        for entry in history[-10:]:
            nick = entry.get("nick", "—")
            pos = entry.get("position", "—")
            date_str = entry.get("dismissed_at", "—")
            warns = entry.get("warns_cleared", 0)
            remover = entry.get("removed_by", "Руководство проекта")
            val = f"📅 Дата: `{date_str}`\n👮 Снял: `{remover}`\n🧹 Очищено варнов: **{warns}** шт."
            if entry.get("reason"):
                val += f"\n📋 Причина: {entry['reason']}"
            embed.add_field(
                name=f"👤 {nick} — {pos}",
                value=val,
                inline=False,
            )

        embed.set_footer(text="WeriGrief Staff System • Префикс: !dismissed / Слэш: /dismissed")
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="unstaff",
        aliases=["dismiss"],
        description="Снять сотрудника с должности персонала (для руководства)",
    )
    @app_commands.describe(
        target="Никнейм в игре, @Discord или тег сотрудника",
        reason="Причина снятия (например: ПСЖ / Неактив)",
    )
    async def cmd_unstaff(self, ctx: commands.Context, target: str, *, reason: str = "Решение руководства"):
        """Снимает сотрудника с должности, аннулирует роли и права."""
        await ctx.defer()
        guild = ctx.guild or self.bot.get_guild(GUILD_ID)
        if not guild:
            await ctx.send("❌ Сервер не найден.")
            return

        if not self.staff_resolver.is_leadership(ctx.author, guild):
            await ctx.send("❌ Команда доступна только **Владельцам**, **Со-владельцам** и **Кураторам** проекта.", ephemeral=True)
            return

        if not self.sheets_monitor:
            await ctx.send("❌ Система Google Sheets еще инициализируется.")
            return

        mc_nick, member = self.staff_resolver.resolve_query_to_target(guild, target)
        author_nick, _ = self.staff_resolver.resolve_query_to_target(guild, ctx.author)

        removed_info = self.sheets_monitor.remove_staff_member(mc_nick, removed_by=author_nick, reason=reason)
        if not removed_info:
            await ctx.send(f"❌ Сотрудник `{mc_nick}` не найден в активном списке персонала таблицы «Списки».")
            return

        if member:
            await StaffRoleManager.strip_discord_staff_roles(
                member, reason=f"Сотрудник снят с должности администратором {author_nick}"
            )
            await EmbedBuilder.send_dismissal_dm(
                member,
                nick=removed_info["nick"],
                position=removed_info.get("position", "Сотрудник"),
                removed_by=author_nick,
                reason=reason,
            )

        await MinecraftPermissionsManager.revoke_minecraft_permissions(mc_nick)

        staff_channel = self.bot.get_channel(STAFF_NOTIFY_CHANNEL)
        embed = EmbedBuilder.build_staff_removed_embed(
            nick=removed_info["nick"],
            position=removed_info.get("position", "Сотрудник"),
            warns_cleared=removed_info.get("warns_cleared", 0),
            removed_by=author_nick,
            reason=reason,
        )

        ping_text = f"{member.mention} " if member else ""
        if staff_channel:
            await staff_channel.send(content=ping_text, embed=embed)

        await ctx.send(f"✅ Сотрудник `{removed_info['nick']}` успешно снят с должности. Уведомление отправлено в <#{STAFF_NOTIFY_CHANNEL}>.")

    @commands.hybrid_command(
        name="appoint",
        aliases=["addstaff"],
        description="Назначить сотрудника в персонал (для руководства)",
    )
    @app_commands.describe(
        target="Никнейм в игре или @Discord сотрудника",
        position="Должность (например: Хелпер / Модератор / Кандидат)",
        discord_name="Имя Discord (если отличается от target)",
        comment="Комментарий или заметка к назначению",
    )
    async def cmd_appoint(
        self,
        ctx: commands.Context,
        target: str,
        position: str,
        discord_name: str = "",
        *,
        comment: str = "",
    ):
        """Назначает нового сотрудника в Google Sheets, Discord и Minecraft."""
        await ctx.defer()
        guild = ctx.guild or self.bot.get_guild(GUILD_ID)
        if not guild:
            await ctx.send("❌ Сервер не найден.")
            return

        if not self.staff_resolver.is_leadership(ctx.author, guild):
            await ctx.send("❌ Команда доступна только **Владельцам**, **Со-владельцам** и **Кураторам** проекта.", ephemeral=True)
            return

        if not self.sheets_monitor:
            await ctx.send("❌ Система Google Sheets еще инициализируется.")
            return

        mc_nick, member = self.staff_resolver.resolve_query_to_target(guild, target)
        author_nick, _ = self.staff_resolver.resolve_query_to_target(guild, ctx.author)
        disc_tag = discord_name or (member.name if member else "")

        added_info = self.sheets_monitor.add_staff_member(
            nick=mc_nick,
            position=position,
            discord_name=disc_tag,
            comment=comment,
            appointed_by=author_nick,
        )
        if not added_info:
            await ctx.send(f"❌ Не удалось добавить `{mc_nick}` в таблицу «Списки» (возможно, уже в списке).")
            return

        roles_granted, roles_blocked = [], []
        if member:
            roles_granted, roles_blocked = await StaffRoleManager.grant_discord_staff_roles(
                member, position=position, reason=f"Назначен на должность {position} администратором {author_nick}"
            )

        mc_granted, mc_group = await MinecraftPermissionsManager.grant_minecraft_permissions(mc_nick, position)

        staff_channel = self.bot.get_channel(STAFF_NOTIFY_CHANNEL)
        embed = EmbedBuilder.build_staff_appointed_embed(
            nick=mc_nick,
            position=position,
            discord_name=disc_tag,
            appointed_by=author_nick,
            comment=comment,
            roles_granted=roles_granted,
            roles_blocked=roles_blocked,
            mc_group=mc_group,
            mc_granted=mc_granted,
        )

        ping_text = f"{member.mention} " if member else ""
        if staff_channel:
            await staff_channel.send(content=ping_text, embed=embed)

        status_parts = [f"✅ Сотрудник `{mc_nick}` успешно назначен на должность **{position}**."]
        if roles_granted:
            status_parts.append(f"Выданы роли Discord: {', '.join(roles_granted)}.")
        if roles_blocked:
            status_parts.append(f"⚠️ **Не удалось выдать роли**: {', '.join(roles_blocked)} (поднимите роль бота выше в настройках).")
        if mc_group:
            status_parts.append(f"Права в Minecraft выданы (группа `{mc_group}`).")
        status_parts.append(f"Уведомление отправлено в <#{STAFF_NOTIFY_CHANNEL}>.")
        await ctx.send(" ".join(status_parts))

