"""
Cog for staff roster management (/staff, /dismissed, /appoint, /unstaff).
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
from src.core.config import GUILD_ID, STAFF_NOTIFY_CHANNEL, LEADERSHIP_IDS
from src.utils.embeds import EmbedBuilder
from src.services.permissions import (
    StaffRoleManager, MinecraftPermissionsManager, POSITION_ORDER
)


class StaffCog(commands.Cog, name="Staff"):
    """Команды управления составом персонала и кадровым реестром."""

    def __init__(self, bot, sheets_monitor, staff_resolver):
        self.bot = bot
        self.sheets_monitor = sheets_monitor
        self.staff_resolver = staff_resolver

    @commands.hybrid_command(
        name="staff",
        description="Показать официальный состав персонала WeriGrief",
    )
    async def cmd_staff(self, ctx: commands.Context):
        """Красивый реестр персонала из Google Sheets."""
        await ctx.defer()

        if not self.sheets_monitor:
            await ctx.send("❌ Google Sheets ещё инициализируется.")
            return

        author_staff = self.staff_resolver.get_staff_entry_for_member(ctx.author)
        if not author_staff and ctx.author.id not in LEADERSHIP_IDS:
            await ctx.send(
                "❌ **Доступ запрещён:** вас нет в официальном составе Google Sheets.",
                ephemeral=True,
            )
            return

        try:
            staff = list(self.sheets_monitor.get_staff_list() or [])
        except Exception as e:
            await ctx.send(f"❌ Не удалось загрузить персонал из Google Sheets: `{e}`")
            return

        display_order = [
            "CREATOR", "GL.OWNER", "OWNER", "D.OWNER",
            "GL.CURATOR", "CURATOR", "Z.CURATOR",
            "GL.ADMIN", "ADMIN", "ML.ADMIN", "D.ADMIN",
            "GL.MODER", "ST.MODER", "MODER", "ML.MODER", "D.MODER",
            "HELPER", "D.HELPER",
            "Гл.Разработчик", "Разработчик", "PR Manager",
            "Сотрудник проекта", "На испытании", "Кандидат",
        ]
        rank = {name.casefold(): i for i, name in enumerate(display_order)}

        groups = []
        section_name = "Основной состав"
        section_entries = []

        for entry in staff:
            if not isinstance(entry, dict):
                continue

            if entry.get("section"):
                if section_entries:
                    groups.append((section_name, section_entries))
                section_name = str(entry["section"]).strip() or "Состав"
                section_entries = []
                continue

            if str(entry.get("nick") or "").strip():
                section_entries.append(entry)

        if section_entries:
            groups.append((section_name, section_entries))

        total = sum(len(entries) for _, entries in groups)

        if total == 0:
            embed = discord.Embed(
                title="✦ WeriGrief | STAFF DIRECTORY",
                description=(
                    "Официальный реестр персонала\n\n"
                    "В Google Sheets не найдено ни одной записи с заполненным ником. "
                    "Проверьте столбец `nick` и результат загрузки таблицы."
                ),
                color=0x19324D,
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(text="WERIGRIEF • STAFF SYSTEM")
            await ctx.send(embed=embed)
            return

        fields = []

        for name, entries in groups:
            entries.sort(
                key=lambda item: rank.get(
                    str(item.get("position") or "").strip().casefold(),
                    len(display_order),
                )
            )

            lines = []
            for entry in entries:
                nick = str(entry.get("nick") or "").strip()
                position = str(entry.get("position") or "Сотрудник").strip()
                discord_name = str(entry.get("discord") or "").strip()
                hours = str(entry.get("hours") or "0").strip()
                warns = str(entry.get("warns") or "0").strip()

                line = f"**{nick}**  ·  `{position}`"
                details = []

                if discord_name:
                    details.append(f"Discord: `{discord_name}`")
                details.append(f"⏱ {hours} ч.")
                details.append(f"⚠️ {warns}")

                line += "\n└ " + "  •  ".join(details)
                lines.append(line)

            chunk = ""
            chunk_number = 1

            for line in lines:
                candidate = f"{chunk}\n\n{line}" if chunk else line
                if len(candidate) > 950 and chunk:
                    field_name = name if chunk_number == 1 else f"{name} · продолжение"
                    fields.append((f"╭・{field_name}"[:256], chunk))
                    chunk_number += 1
                    chunk = line
                else:
                    chunk = candidate

            if chunk:
                field_name = name if chunk_number == 1 else f"{name} · продолжение"
                fields.append((f"╭・{field_name}"[:256], chunk))

        description = (
            "### Официальный состав проекта\n"
            f"**{total}** сотрудников в реестре\n"
            "Источник данных: **Google Sheets**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        pages = []
        current_fields = []
        current_size = len(description)

        for field_name, field_value in fields:
            field_size = len(field_name) + len(field_value)
            if current_fields and (
                len(current_fields) >= 20 or current_size + field_size > 5400
            ):
                pages.append(current_fields)
                current_fields = []
                current_size = 0

            current_fields.append((field_name, field_value))
            current_size += field_size

        if current_fields:
            pages.append(current_fields)

        for page_index, page_fields in enumerate(pages, start=1):
            embed = discord.Embed(
                title="✦ WeriGrief  /  STAFF DIRECTORY",
                description=description if page_index == 1 else (
                    f"**Официальный состав проекта** · {total} сотрудников"
                ),
                color=0x19324D,
                timestamp=datetime.now(timezone.utc),
            )

            for field_name, field_value in page_fields:
                embed.add_field(
                    name=field_name,
                    value=field_value,
                    inline=False,
                )

            embed.set_footer(
                text=f"WERIGRIEF STAFF SYSTEM  •  PAGE {page_index}/{len(pages)}"
            )
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
        name="promote",
        description="Повысить сотрудника и выбрать новую должность",
    )
    @app_commands.choices(position=[app_commands.Choice(name=role, value=role) for role in POSITION_ORDER])
    @app_commands.describe(
        target="Сотрудник: ник Minecraft или Discord-упоминание",
        position="Выберите новую должность выше текущей",
        discord_member="Выберите сотрудника из списка (необязательно)",
    )
    async def cmd_promote(
        self, ctx: commands.Context, target: str, position: str,
        discord_member: discord.Member | None = None
    ):
        await ctx.defer()
        guild = ctx.guild or self.bot.get_guild(GUILD_ID)
        if not guild:
            await ctx.send("❌ Сервер не найден.")
            return
        if not self.staff_resolver.is_leadership(ctx.author, guild):
            await ctx.send("❌ Команда доступна только руководству проекта.", ephemeral=True)
            return
        mc_nick, member = self.staff_resolver.resolve_query_to_target(guild, target)
        if discord_member is not None:
            member = discord_member
            linked = self.staff_resolver.get_staff_entry_for_member(member)
            if linked and linked.get("nick"):
                mc_nick = linked["nick"]
        entry = next((x for x in self.sheets_monitor.get_staff_list()
                      if x.get("nick", "").lower() == mc_nick.lower()), None)
        if not entry:
            await ctx.send(f"❌ `{mc_nick}` не найден в активном составе.")
            return
        current = str(entry.get("position", "")).strip()
        idx = next((i for i, x in enumerate(POSITION_ORDER)
                    if x.casefold() == current.casefold()), None)
        if idx is None:
            await ctx.send(f"❌ Не удалось определить текущую должность `{current}`. Используй `/appoint` для выбора должности.")
            return
        if idx >= len(POSITION_ORDER) - 1:
            await ctx.send("❌ Это уже максимальная должность.")
            return
        new_position = position
        new_idx = next((i for i, x in enumerate(POSITION_ORDER)
                        if x.casefold() == new_position.casefold()), None)
        if new_idx is None or new_idx <= idx:
            await ctx.send("❌ Для повышения выбери должность выше текущей.")
            return
        updated = self.sheets_monitor.update_staff_position(mc_nick, new_position)
        if not updated:
            await ctx.send("❌ Не удалось обновить должность в Google Sheets.")
            return
        roles_granted, roles_blocked = [], []
        if member:
            roles_granted, roles_blocked = await StaffRoleManager.grant_discord_staff_roles(
                member, position=new_position, old_position=current,
                reason=f"Повышение {mc_nick}: {current} -> {new_position}"
            )
        mc_granted, mc_group = await MinecraftPermissionsManager.grant_minecraft_permissions(mc_nick, new_position)
        status = f"✅ `{mc_nick}` повышен: **{current} → {new_position}**."
        if roles_granted:
            status += f" Выданы роли: {', '.join(roles_granted)}."
        if roles_blocked:
            status += f" ⚠️ Не удалось выдать: {', '.join(roles_blocked)}."
        if mc_group and mc_granted:
            status += f" Команда установки группы LuckPerms `{mc_group}` отправлена в консоль Minecraft; проверьте ответ консоли в логе бота."
        elif mc_group:
            status += f" ⚠️ Команда LuckPerms `{mc_group}` не подтверждена Pterodactyl API."
        staff_channel = self.bot.get_channel(STAFF_NOTIFY_CHANNEL)
        embed = EmbedBuilder.build_staff_promoted_embed(
            nick=mc_nick, old_pos=current, new_pos=new_position,
            promoted_by=ctx.author.name,
            discord_name=member.name if member else entry.get("discord", ""),
            roles_granted=roles_granted, roles_blocked=roles_blocked,
            mc_group=mc_group, mc_granted=mc_granted,
        )
        if staff_channel:
            await staff_channel.send(content=member.mention if member else None, embed=embed)
        await ctx.send(status)

    @commands.hybrid_command(
        name="demote",
        description="Понизить сотрудника и выбрать новую должность",
    )
    @app_commands.choices(position=[app_commands.Choice(name=role, value=role) for role in POSITION_ORDER])
    @app_commands.describe(
        target="Сотрудник: ник Minecraft или Discord-упоминание",
        position="Выберите новую должность ниже текущей",
        discord_member="Выберите сотрудника из списка (необязательно)",
    )
    async def cmd_demote(
        self, ctx: commands.Context, target: str, position: str,
        discord_member: discord.Member | None = None
    ):
        await ctx.defer()
        guild = ctx.guild or self.bot.get_guild(GUILD_ID)
        if not guild:
            await ctx.send("❌ Сервер не найден.")
            return
        if not self.staff_resolver.is_leadership(ctx.author, guild):
            await ctx.send("❌ Команда доступна только руководству проекта.", ephemeral=True)
            return
        mc_nick, member = self.staff_resolver.resolve_query_to_target(guild, target)
        if discord_member is not None:
            member = discord_member
            linked = self.staff_resolver.get_staff_entry_for_member(member)
            if linked and linked.get("nick"):
                mc_nick = linked["nick"]
        entry = next((x for x in self.sheets_monitor.get_staff_list()
                      if x.get("nick", "").lower() == mc_nick.lower()), None)
        if not entry:
            await ctx.send(f"❌ `{mc_nick}` не найден в активном составе.")
            return
        current = str(entry.get("position", "")).strip()
        idx = next((i for i, x in enumerate(POSITION_ORDER)
                    if x.casefold() == current.casefold()), None)
        if idx is None:
            await ctx.send(f"❌ Не удалось определить текущую должность `{current}`.")
            return
        if idx <= 0:
            await ctx.send("❌ Это уже минимальная должность.")
            return
        new_position = position
        new_idx = next((i for i, x in enumerate(POSITION_ORDER)
                        if x.casefold() == new_position.casefold()), None)
        if new_idx is None or new_idx >= idx:
            await ctx.send("❌ Для понижения выбери должность ниже текущей.")
            return
        updated = self.sheets_monitor.update_staff_position(mc_nick, new_position)
        if not updated:
            await ctx.send("❌ Не удалось обновить должность в Google Sheets.")
            return
        roles_granted, roles_blocked = [], []
        if member:
            roles_granted, roles_blocked = await StaffRoleManager.grant_discord_staff_roles(
                member, position=new_position, old_position=current,
                reason=f"Понижение {mc_nick}: {current} -> {new_position}"
            )
        mc_granted, mc_group = await MinecraftPermissionsManager.grant_minecraft_permissions(mc_nick, new_position)
        status = f"✅ `{mc_nick}` понижен: **{current} → {new_position}**."
        if roles_granted:
            status += f" Выданы роли: {', '.join(roles_granted)}."
        if roles_blocked:
            status += f" ⚠️ Не удалось выдать: {', '.join(roles_blocked)}."
        if mc_group and mc_granted:
            status += f" Команда установки группы LuckPerms `{mc_group}` отправлена в консоль Minecraft; проверьте ответ консоли в логе бота."
        elif mc_group:
            status += f" ⚠️ Команда LuckPerms `{mc_group}` не подтверждена Pterodactyl API."
        staff_channel = self.bot.get_channel(STAFF_NOTIFY_CHANNEL)
        embed = EmbedBuilder.build_staff_promoted_embed(
            nick=mc_nick, old_pos=current, new_pos=new_position,
            promoted_by=ctx.author.name,
            discord_name=member.name if member else entry.get("discord", ""),
            roles_granted=roles_granted, roles_blocked=roles_blocked,
            mc_group=mc_group, mc_granted=mc_granted,
        )
        embed.title = "📉 Понижение в должности персонала"
        embed.color = 0xE67E22
        if staff_channel:
            await staff_channel.send(content=member.mention if member else None, embed=embed)
        await ctx.send(status)

    @commands.hybrid_command(
        name="vacation",
        aliases=["отпуск"],
        description="Оформить отпуск от 1 до 15 дней",
    )
    @app_commands.describe(days="Количество дней отпуска (максимум 15)")
    async def cmd_vacation(self, ctx: commands.Context, days: int):
        await ctx.defer(ephemeral=True)
        if not self.sheets_monitor:
            await ctx.send("❌ Google Sheets ещё инициализируется.", ephemeral=True)
            return
        staff_entry = self.staff_resolver.get_staff_entry_for_member(ctx.author)
        if not staff_entry:
            await ctx.send("❌ Оформить отпуск могут только сотрудники из действующего состава.", ephemeral=True)
            return
        nick = staff_entry.get("nick", "")
        try:
            vacation = self.sheets_monitor.create_vacation(nick, days)
        except ValueError as exc:
            await ctx.send(f"❌ {exc}", ephemeral=True)
            return
        from datetime import date
        start_date = date.fromisoformat(vacation["start"]).strftime("%d.%m.%Y")
        end_date = date.fromisoformat(vacation["end"]).strftime("%d.%m.%Y")
        embed = discord.Embed(
            title="🏖️ Отпуск сотрудника оформлен",
            color=0x3498DB,
            description=f"Для сотрудника `{nick}` оформлен отпуск на **{days} дней**.",
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="📅 Начало", value=f"`{start_date}`", inline=True)
        embed.add_field(name="📅 Окончание", value=f"`{end_date}`", inline=True)
        embed.add_field(name="📌 Статус нормы", value="Освобождение от суточной нормы активно на период отпуска", inline=False)
        embed.add_field(name="👮 Оформил", value=ctx.author.mention, inline=True)
        embed.set_footer(text="WeriGrief Staff System • Отпуск")
        await ctx.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="unstaff",
        description="Снять сотрудника с должности персонала (для руководства)",
    )
    @app_commands.describe(
        target="Никнейм в игре, @Discord или тег сотрудника",
        reason="Причина снятия (например: ПСЖ / Неактив)",
    )
    async def cmd_unstaff(
        self,
        interaction: discord.Interaction,
        target: str,
        reason: str = "Решение руководства",
    ):
        """Отдельная slash-команда снятия сотрудника с Google Sheets, Discord и Minecraft."""
        await interaction.response.defer(thinking=True)
        guild = interaction.guild or self.bot.get_guild(GUILD_ID)
        if not guild:
            await interaction.followup.send("❌ Сервер не найден.")
            return

        if not self.staff_resolver.is_leadership(interaction.user, guild):
            await interaction.followup.send(
                "❌ Команда доступна только **Владельцам**, **Со-владельцам** и **Кураторам** проекта.",
                ephemeral=True,
            )
            return

        if not self.sheets_monitor:
            await interaction.followup.send("❌ Система Google Sheets еще инициализируется.")
            return

        mc_nick, member = self.staff_resolver.resolve_query_to_target(guild, target)
        author_nick, _ = self.staff_resolver.resolve_query_to_target(guild, interaction.user)
        removed_info = self.sheets_monitor.remove_staff_member(
            mc_nick, removed_by=author_nick, reason=reason
        )
        if not removed_info:
            await interaction.followup.send(
                f"❌ Сотрудник `{mc_nick}` не найден в активном списке персонала таблицы «Списки»."
            )
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
        await interaction.followup.send(
            f"✅ Сотрудник `{removed_info['nick']}` успешно снят с должности. "
            f"Уведомление отправлено в <#{STAFF_NOTIFY_CHANNEL}>."
        )

    @commands.hybrid_command(
        name="appoint",
        aliases=["addstaff"],
        description="Назначить сотрудника в персонал (для руководства)",
    )
    @app_commands.choices(position=[
        app_commands.Choice(name=role, value=role) for role in POSITION_ORDER
    ])
    @app_commands.describe(
        target="Никнейм в игре или @Discord сотрудника",
        position="Выберите должность из списка",
        discord_name="Discord username (необязательно)",
        discord_member="Выберите Discord-пользователя из списка (необязательно)",
    )
    async def cmd_appoint(
        self,
        ctx: commands.Context,
        target: str,
        position: str,
        discord_name: str = "",
        discord_member: discord.Member | None = None,
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
        if discord_member is not None:
            member = discord_member
        author_nick, _ = self.staff_resolver.resolve_query_to_target(guild, ctx.author)
        disc_tag = (member.name if member else discord_name).strip().lstrip("@")

        added_info = self.sheets_monitor.add_staff_member(
            nick=mc_nick,
            position=position,
            discord_name=disc_tag,
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
            comment="",
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
        if mc_group and mc_granted:
            status_parts.append(f"Команда установки LuckPerms `{mc_group}` отправлена в консоль Minecraft; проверьте ответ консоли в логе бота.")
        elif mc_group:
            status_parts.append(f"⚠️ Не удалось подтвердить отправку команды LuckPerms `{mc_group}`.")
        status_parts.append(f"Уведомление отправлено в <#{STAFF_NOTIFY_CHANNEL}>.")
        await ctx.send(" ".join(status_parts))
