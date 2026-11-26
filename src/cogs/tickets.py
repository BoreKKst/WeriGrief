"""Private support and application ticket workflow for WeriTime."""

import hashlib
import logging
import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from src.core.config import GUILD_ID, SUPPORT_ROLE_ID
from src.core.database import get_pool
from src.services.permissions import STAFF_ROLE_IDS

log = logging.getLogger("weritime.tickets")

PANEL_CATEGORY = "🛟・ПОДДЕРЖКА"
TICKETS_CATEGORY_ID = 1554801084709601300
APPLICATION_PANEL_CHANNEL = "📝・набор"
SUPPORT_PANEL_CHANNEL = "🛠️・поддержка"
CLOSED_CATEGORY = "🗄️・закрытые-обращения"
TICKET_CATEGORIES = {
    "helper": "🛡️・заявки-хелпер",
    "media": "🎨・заявки-медиа",
    "question": "❓・вопросы",
    "bug": "🐛・баги",
    "complaint": "🚨・жалобы",
}
LEGACY_CHANNELS = {"медиа", "хелпер", "『🎥』ʍᴇдиᴀ", "『🛡️』хᴇᴧᴨᴇᴩ"}


class TicketDetailsModal(discord.ui.Modal):
    def __init__(self, cog, kind: str, target: str = ""):
        title = {
            "helper": "Анкета • Хелпер",
            "media": "Анкета • Медиа",
            "question": "Подробности вопроса",
            "bug": "Подробности ошибки",
            "complaint": "Подробности жалобы",
        }[kind]
        super().__init__(title=title)
        self.cog = cog
        self.kind = kind
        self.target = target
        paragraph = discord.TextStyle.paragraph
        self.specs = {
            "helper": [
                ("nick", "Твой Minecraft-ник", "Укажи точный ник", True, 32, None),
                ("availability", "Часовой пояс и онлайн", "Когда обычно можешь помогать?", True, 120, None),
                ("experience", "Опыт помощи или модерации", "Расскажи, где и чем помогал", True, 600, paragraph),
                ("motivation", "Почему хочешь стать Хелпером?", "Чем будешь полезен команде?", True, 600, paragraph),
                ("scenario", "Как решишь конфликт в чате?", "Опиши действия по шагам", True, 600, paragraph),
            ],
            "media": [
                ("nick", "Ник Minecraft или Discord", "Как с тобой связаться", True, 64, None),
                ("portfolio", "Ссылки на примеры работ", "Портфолио, видео или посты", True, 400, paragraph),
                ("formats", "Какие материалы создаёшь?", "Видео, клипы, посты, графика…", True, 300, paragraph),
                ("experience", "Опыт и инструменты", "Программы, площадки, опыт", False, 400, paragraph),
                ("schedule", "План активности", "Как часто сможешь выпускать материалы?", True, 300, paragraph),
            ],
            "question": [
                ("subject", "Тема вопроса", "Коротко сформулируй вопрос", True, 90, None),
                ("details", "Подробности", "Что произошло и какой ответ нужен?", True, 800, paragraph),
                ("where", "Где возник вопрос?", "Канал, команда или Minecraft-сервер", False, 150, None),
                ("nick", "Ник или ID (если нужен)", "Minecraft-ник / Discord ID", False, 64, None),
            ],
            "bug": [
                ("subject", "Что именно сломалось?", "Короткое название ошибки", True, 90, None),
                ("location", "Где произошла ошибка?", "Сервер, Discord, команда или меню", True, 150, None),
                ("steps", "Как повторить ошибку?", "Шаги, которые приводят к проблеме", True, 700, paragraph),
                ("result", "Ожидаемый и фактический результат", "Что должно было быть и что вышло", True, 700, paragraph),
                ("nick", "Ник и версия (если Minecraft)", "Ник / версия игры или лаунчера", False, 100, None),
            ],
            "complaint": [
                ("subject", "Коротко о жалобе", "Например: оскорбления или нарушение правил", True, 90, None),
                ("target_name", "На кого жалоба?", "Ник, Discord-имя или ID", True, 100, None),
                ("details", "Что произошло?", "Опиши факты и последовательность событий", True, 800, paragraph),
                ("date_time", "Когда это произошло?", "Примерная дата, время и часовой пояс", False, 100, None),
                ("evidence", "Какие есть доказательства?", "Ссылки; файлы можно приложить в тикете", False, 250, paragraph),
            ],
        }[kind]
        self.fields = {}
        for key, label, placeholder, required, limit, style in self.specs:
            field = discord.ui.TextInput(
                label=label,
                placeholder=placeholder,
                required=required,
                max_length=limit,
                style=style or discord.TextStyle.short,
                min_length=10 if required and style == paragraph else None,
            )
            self.fields[key] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = await self.cog.open_ticket(
                interaction.guild,
                interaction.user,
                self.kind,
                str(self.fields.get("subject") or {
                    "helper": "Заявка на Хелпера", "media": "Заявка в Медиа",
                    "question": "Вопрос", "bug": "Сообщение об ошибке", "complaint": "Жалоба",
                }[self.kind]),
                "\n\n".join(
                    f"**{field.label}:** {str(field)}" for key, field in self.fields.items()
                    if key not in {"subject", "nick"} and str(field).strip()
                ),
                str(self.fields.get("nick", "")),
                self.target,
            )
            await interaction.followup.send(f"✅ Обращение создано: {channel.mention}", ephemeral=True)
        except Exception as exc:
            log.exception("Could not create ticket")
            await interaction.followup.send(f"❌ Не получилось открыть обращение: {exc}", ephemeral=True)


class ComplaintTargetView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=120)
        self.cog = cog
        select = discord.ui.Select(
            placeholder="На кого подать жалобу?",
            min_values=1,
            max_values=1,
            custom_id="weritime:tickets:complaint-target",
            options=[
                discord.SelectOption(label="На администрацию", value="administration", emoji="🛡️"),
                discord.SelectOption(label="На участника", value="member", emoji="👤"),
            ],
        )
        select.callback = self.choose_target
        self.add_item(select)

    async def choose_target(self, interaction: discord.Interaction):
        target = interaction.data["values"][0]
        await interaction.response.send_modal(TicketDetailsModal(self.cog, "complaint", target))


class TicketPanelView(discord.ui.View):
    def __init__(self, cog, mode: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.mode = mode
        options = [
            discord.SelectOption(label="Заявка на Хелпера", value="helper", emoji="🛡️", description="Хочешь помогать участникам"),
            discord.SelectOption(label="Заявка в Медиа", value="media", emoji="🎨", description="Видео, посты и материалы проекта"),
            discord.SelectOption(label="Вопрос", value="question", emoji="❓", description="Нужна помощь администрации"),
            discord.SelectOption(label="Сообщить об ошибке", value="bug", emoji="🐛", description="Опиши баг и приложи скриншот в тикет"),
            discord.SelectOption(label="Жалоба", value="complaint", emoji="🚨", description="На администрацию или участника"),
        ]
        select = discord.ui.Select(
            placeholder="Выбери тип заявки" if mode == "applications" else "Выбери тип поддержки",
            min_values=1,
            max_values=1,
            custom_id=f"weritime:tickets:open:{mode}",
            options=options[:2] if mode == "applications" else options[2:],
        )
        select.callback = self.open
        self.add_item(select)

    async def open(self, interaction: discord.Interaction):
        kind = interaction.data["values"][0]
        if kind == "complaint":
            await interaction.response.send_message(
                "Выбери, на кого подать жалобу:",
                view=ComplaintTargetView(self.cog),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(TicketDetailsModal(self.cog, kind))


class TicketCloseReasonModal(discord.ui.Modal, title="Закрыть обращение"):
    reason = discord.ui.TextInput(label="Причина закрытия", style=discord.TextStyle.paragraph, min_length=3, max_length=400)

    def __init__(self, cog, channel_id: int, opener_id: int):
        super().__init__()
        self.cog, self.channel_id, self.opener_id = cog, channel_id, opener_id

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.close_ticket(interaction, self.channel_id, self.opener_id, str(self.reason))


class TicketTransferView(discord.ui.View):
    def __init__(self, cog, channel_id: int, opener_id: int):
        super().__init__(timeout=90)
        self.cog, self.channel_id, self.opener_id = cog, channel_id, opener_id
        select = discord.ui.UserSelect(placeholder="Передать сотруднику поддержки", min_values=1, max_values=1)
        select.callback = self.transfer
        self.add_item(select)

    async def transfer(self, interaction: discord.Interaction):
        staff = interaction.data["values"][0]
        await self.cog.transfer_ticket(interaction, self.channel_id, self.opener_id, int(staff))


class TicketCloseView(discord.ui.View):
    def __init__(self, cog, channel_id: int, opener_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id
        self.opener_id = opener_id
        button = discord.ui.Button(
            label="Закрыть обращение",
            emoji="🔒",
            style=discord.ButtonStyle.danger,
            custom_id=f"weritime:tickets:close:{channel_id}:{opener_id}",
        )
        button.callback = self.close
        self.add_item(button)
        transfer = discord.ui.Button(
            label="Передать тикет",
            emoji="↗️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"weritime:tickets:transfer:{channel_id}:{opener_id}",
        )
        transfer.callback = self.transfer
        self.add_item(transfer)

    async def close(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketCloseReasonModal(self.cog, self.channel_id, self.opener_id))

    async def transfer(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not any(r.id in STAFF_ROLE_IDS for r in member.roles):
            await interaction.response.send_message("Передавать тикеты может команда поддержки.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Кому передать обращение?", view=TicketTransferView(self.cog, self.channel_id, self.opener_id), ephemeral=True
        )


class TicketsCog(commands.Cog, name="Tickets"):
    def __init__(self, bot):
        self.bot = bot
        self.categories: dict[str, discord.CategoryChannel] = {}
        self.panel_channel: discord.TextChannel | None = None

    @staticmethod
    def support_role(guild: discord.Guild):
        return guild.get_role(SUPPORT_ROLE_ID) or discord.utils.get(
            guild.roles, name="🎧・Поддержка"
        ) or discord.utils.get(guild.roles, name="Поддержка")

    async def cog_load(self):
        try:
            await self.ensure_structure()
        except discord.HTTPException:
            log.exception("Could not create ticket categories/panel")

    async def ensure_table(self, conn):
        await conn.execute("CREATE SCHEMA IF NOT EXISTS store")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS store.support_tickets (
                channel_id BIGINT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                opener_id BIGINT NOT NULL,
                ticket_type TEXT NOT NULL,
            assigned_to BIGINT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at DOUBLE PRECISION NOT NULL,
                closed_at DOUBLE PRECISION
            )
        """)
        await conn.execute("ALTER TABLE store.support_tickets ADD COLUMN IF NOT EXISTS assigned_to BIGINT")

    async def ensure_structure(self):
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None or not guild.me or not guild.me.guild_permissions.manage_channels:
            return

        # Remove only the two explicitly replaced legacy application channels.
        for channel in list(guild.text_channels):
            if channel.name.casefold() in LEGACY_CHANNELS:
                await channel.delete(reason="Заменён новой системой заявок WeriTime")

        ticket_category = guild.get_channel(TICKETS_CATEGORY_ID)
        if not isinstance(ticket_category, discord.CategoryChannel):
            raise RuntimeError(f"Не найдена категория тикетов Discord ID {TICKETS_CATEGORY_ID}")
        self.categories["panel"] = ticket_category
        for kind in TICKET_CATEGORIES:
            self.categories[kind] = ticket_category

        closed = discord.utils.get(guild.categories, name=CLOSED_CATEGORY)
        if closed is None:
            private_overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True,
                    manage_channels=True,
                ),
            }
            for role in guild.roles:
                if role.id in STAFF_ROLE_IDS or role == self.support_role(guild):
                    private_overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True,
                        manage_messages=True,
                    )
            closed = await guild.create_category(
                CLOSED_CATEGORY, overwrites=private_overwrites,
                reason="Настройка закрытых обращений WeriTime",
            )
        self.categories["closed"] = closed

        legacy_names = {"📨・помощь WeriTime", *TICKET_CATEGORIES.values()}
        for old_category in list(guild.categories):
            if old_category.id == ticket_category.id or old_category.name not in legacy_names:
                continue
            for child in list(old_category.channels):
                if child.name in {APPLICATION_PANEL_CHANNEL, SUPPORT_PANEL_CHANNEL}:
                    continue
                await child.edit(
                    category=ticket_category, sync_permissions=False,
                    reason="Перенос обращений в указанную категорию поддержки",
                )
                support_role = self.support_role(guild)
                if support_role and isinstance(child, discord.TextChannel):
                    await child.set_permissions(
                        support_role, view_channel=True, send_messages=True,
                        read_message_history=True, attach_files=True,
                        reason="Доступ команды поддержки к перенесённому тикету",
                    )

        panel_channels = (
            ("applications", APPLICATION_PANEL_CHANNEL, "Заявки на Хелпера и Медиа-команду"),
            ("support", SUPPORT_PANEL_CHANNEL, "Вопросы, ошибки и жалобы"),
        )
        panel_overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True, send_messages=False, read_message_history=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                embed_links=True, attach_files=True,
            ),
        }
        support_role = self.support_role(guild)
        for role in guild.roles:
            if role.id in STAFF_ROLE_IDS or role == support_role:
                panel_overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True,
                    manage_messages=True,
                )
        for mode, channel_name, topic in panel_channels:
            panel = discord.utils.get(guild.text_channels, name=channel_name)
            if panel is None:
                panel = await guild.create_text_channel(
                    channel_name, category=ticket_category, overwrites=panel_overwrites, topic=topic,
                    reason="Создание панели обращений WeriTime",
                )
            else:
                await panel.edit(
                    category=ticket_category, overwrites=panel_overwrites,
                    sync_permissions=False,
                    reason="Панели обращений видны участникам в категории поддержки",
                )
            self.bot.add_view(TicketPanelView(self, mode))

        for old_category in list(guild.categories):
            if old_category.id != ticket_category.id and old_category.name in legacy_names and not old_category.channels:
                await old_category.delete(reason="Обращения объединены в категорию поддержки")

        async with get_pool().acquire() as conn:
            await self.ensure_table(conn)
            rows = await conn.fetch(
                "SELECT channel_id, opener_id FROM store.support_tickets WHERE guild_id=$1 AND status='open'",
                guild.id,
            )
        for row in rows:
            if guild.get_channel(row["channel_id"]):
                self.bot.add_view(TicketCloseView(self, row["channel_id"], row["opener_id"]))

        for mode, channel_name, topic in panel_channels:
            panel = discord.utils.get(guild.text_channels, name=channel_name)
            marker = f"WERITIME_TICKET_PANEL_V1_{mode}"
            exists = False
            async for message in panel.history(limit=50):
                if message.author.id == self.bot.user.id and marker in message.content:
                    exists = True
                    break
            if exists:
                continue
            embed = discord.Embed(
                title="📝 Набор в команду WeriTime" if mode == "applications" else "🛠️ Поддержка WeriTime",
                description=(
                    "Подай заявку на Хелпера или Медиа-команду. Бот создаст приватный канал, где команда задаст уточняющие вопросы."
                    if mode == "applications" else
                    "Выбери вопрос, сообщение об ошибке или жалобу. Для жалобы сначала укажи, на кого она — на администрацию или участника."
                ),
                color=0x5865F2 if mode == "applications" else 0xE67E22,
            )
            embed.set_footer(text="WeriTime • приватное обращение; фотографии прикрепляй в созданном канале")
            await panel.send(content=marker, embed=embed, view=TicketPanelView(self, mode))

    async def open_ticket(self, guild, opener, kind, subject, details, nick, target):
        if guild is None or guild.id != GUILD_ID:
            raise RuntimeError("Открой тикет на официальном сервере WeriTime.")
        async with get_pool().acquire() as conn:
            await self.ensure_table(conn)
            existing = await conn.fetchrow(
                "SELECT channel_id FROM store.support_tickets WHERE guild_id=$1 AND opener_id=$2 AND status='open' LIMIT 1",
                guild.id,
                opener.id,
            )
        if existing:
            channel = guild.get_channel(existing["channel_id"])
            if channel:
                raise RuntimeError(f"У тебя уже есть открытый тикет: {channel.mention}")

        category = self.categories[kind]
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            opener: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_channels=True, manage_messages=True, attach_files=True,
            ),
        }
        for role in guild.roles:
            if role.id in STAFF_ROLE_IDS or role == self.support_role(guild):
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True,
                    manage_messages=True, attach_files=True,
                )

        safe_name = re.sub(r"[^a-z0-9а-яё-]+", "-", opener.display_name.casefold()).strip("-")[:35] or str(opener.id)
        channel = await guild.create_text_channel(
            name=f"{kind}-{safe_name}"[:95],
            category=category,
            overwrites=overwrites,
            topic=f"WeriTime ticket | owner={opener.id} | type={kind} | status=open",
            reason=f"Открыто обращение {kind} от пользователя {opener.id}",
        )
        try:
            async with get_pool().acquire() as conn:
                await conn.execute(
                    "INSERT INTO store.support_tickets (channel_id,guild_id,opener_id,ticket_type,status,created_at) VALUES ($1,$2,$3,$4,'open',EXTRACT(EPOCH FROM NOW()))",
                    channel.id, guild.id, opener.id, kind,
                )
        except Exception:
            await channel.delete(reason="Не удалось сохранить обращение в базе")
            raise

        self.bot.add_view(TicketCloseView(self, channel.id, opener.id))
        embed = discord.Embed(
            title=f"{TICKET_CATEGORIES[kind]} · новое обращение",
            color=0x3498DB,
            description=f"**Тема:** {subject}\n\n{details}",
        )
        embed.add_field(name="Автор", value=f"{opener.mention} (`{opener.id}`)", inline=False)
        if nick:
            embed.add_field(name="Minecraft-ник", value=nick[:100], inline=True)
        if target:
            target_label = "Администрация" if target == "administration" else "Участник"
            embed.add_field(name="Жалоба на", value=target_label, inline=True)
        embed.add_field(name="Фото и файлы", value="Можно прикрепить сообщением в этом приватном канале.", inline=False)
        if kind == "helper":
            embed.add_field(
                name="Перед собеседованием прочитай",
                value=(
                    "• Правила Minecraft: <#1572902563290939452>\n"
                    "• Правила Discord: <#1554538230714466315>\n"
                    "• [Правила персонала](https://docs.google.com/document/d/1xQFoEingNzXUsGHZohuRHkNLUyTm0RXQhLcRZNaItx4/edit)\n"
                    "• [Регламент команды](https://docs.google.com/document/d/1fmMMo7C0SF2y1Udnu5Tan2T-nWcW-D4r3IU-aSSMdsw/edit)"
                ),
                inline=False,
            )
        embed.set_footer(text="Закрыть обращение можно кнопкой ниже")
        notice = opener.mention
        allowed_mentions = discord.AllowedMentions(users=[opener], roles=False, everyone=False)
        if kind == "helper":
            support_role = guild.get_role(SUPPORT_ROLE_ID) or discord.utils.get(
                guild.roles, name="🎧・Поддержка"
            ) or discord.utils.get(guild.roles, name="Поддержка")
            if support_role:
                notice += f" {support_role.mention}"
                allowed_mentions = discord.AllowedMentions(
                    users=[opener], roles=[support_role], everyone=False
                )
        await channel.send(
            content=notice,
            embed=embed,
            view=TicketCloseView(self, channel.id, opener.id),
            allowed_mentions=allowed_mentions,
        )
        return channel

    async def transfer_ticket(self, interaction, channel_id: int, opener_id: int, staff_id: int):
        guild = interaction.guild
        channel = guild.get_channel(channel_id) if guild else None
        caller = interaction.user
        if not isinstance(caller, discord.Member) or not any(r.id in STAFF_ROLE_IDS for r in caller.roles):
            await interaction.response.send_message("Передавать тикеты может команда поддержки.", ephemeral=True)
            return
        target = guild.get_member(staff_id) if guild else None
        if not target or not any(r.id in STAFF_ROLE_IDS for r in target.roles):
            await interaction.response.send_message("Выбери участника с ролью поддержки.", ephemeral=True)
            return
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Тикет уже удалён.", ephemeral=True)
            return
        await channel.set_permissions(
            target, view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, reason=f"Тикет передан {caller.id} сотруднику {target.id}",
        )
        async with get_pool().acquire() as conn:
            await conn.execute(
                "UPDATE store.support_tickets SET assigned_to=$2 WHERE channel_id=$1 AND status='open'",
                channel_id, target.id,
            )
        await channel.send(f"↗️ Тикет передан {target.mention}.", allowed_mentions=discord.AllowedMentions(users=True))
        await interaction.response.send_message(f"✅ Передано {target.mention}.", ephemeral=True)

    async def close_ticket(self, interaction, channel_id: int, opener_id: int, reason: str):
        guild = interaction.guild
        channel = guild.get_channel(channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Обращение уже закрыто или удалено.", ephemeral=True)
            return
        member = interaction.user
        is_staff = isinstance(member, discord.Member) and any(role.id in STAFF_ROLE_IDS for role in member.roles)
        if member.id != opener_id and not is_staff:
            await interaction.response.send_message("Закрыть тикет может его автор или команда поддержки.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        transcript = []
        message_count = 0
        async for item in channel.history(limit=1000, oldest_first=True):
            attachments = ",".join(attachment.url for attachment in item.attachments)
            transcript.append(f"{item.id}|{item.created_at.isoformat()}|{item.author.id}|{item.content}|{attachments}")
            message_count += 1
        digest = hashlib.sha256("\n".join(transcript).encode("utf-8")).hexdigest()

        from src.core.config import STAFF_NOTIFY_CHANNEL
        staff_channel = guild.get_channel(STAFF_NOTIFY_CHANNEL)
        if not isinstance(staff_channel, discord.TextChannel):
            await interaction.followup.send("❌ Staff-chat недоступен; тикет оставлен открытым.", ephemeral=True)
            return
        ticket_row = await get_pool().fetchrow(
            "SELECT ticket_type, assigned_to FROM store.support_tickets WHERE channel_id=$1",
            channel_id,
        )
        audit = discord.Embed(
            title="🔒 Тикет закрыт и удалён",
            color=0x7F8C8D,
            description=(
                f"**Канал:** `{channel.name}` (`{channel.id}`)\n"
                f"**Автор:** <@{opener_id}> (`{opener_id}`)\n"
                f"**Закрыл:** {member.mention} (`{member.id}`)\n"
                f"**Причина:** {reason[:500]}\n"
                f"**Тип:** {(ticket_row['ticket_type'] if ticket_row else 'неизвестен')}\n"
                f"**Сообщений в отпечатке:** {message_count}\n"
                f"**SHA-256:** `{digest}`"
            ),
            timestamp=datetime.now(timezone.utc),
        )
        try:
            await staff_channel.send(embed=audit, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            await interaction.followup.send("❌ Не удалось отправить хэш в staff-chat; тикет оставлен открытым.", ephemeral=True)
            return

        async with get_pool().acquire() as conn:
            await conn.execute(
                "UPDATE store.support_tickets SET status='closed', closed_at=EXTRACT(EPOCH FROM NOW()) WHERE channel_id=$1 AND status='open'",
                channel_id,
            )
        await channel.delete(reason=f"Тикет закрыт {member.id}: {reason[:400]}")
        await interaction.followup.send("✅ Тикет удалён; причина и SHA-256 отправлены в staff-chat.", ephemeral=True)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if channel.guild.id != GUILD_ID:
            return
        if channel.name in {APPLICATION_PANEL_CHANNEL, SUPPORT_PANEL_CHANNEL, PANEL_CATEGORY, CLOSED_CATEGORY, *TICKET_CATEGORIES.values()}:
            try:
                await self.ensure_structure()
            except discord.HTTPException:
                log.exception("Could not restore the ticket system structure")
        async with get_pool().acquire() as conn:
            await conn.execute("DELETE FROM store.support_tickets WHERE channel_id=$1", channel.id)


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
