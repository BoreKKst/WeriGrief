"""Система тикетов WeriGrief с постоянной панелью и архивированием."""
import io
import re

import discord
from discord.ext import commands

from src.core.config import SUPPORT_CATEGORY_ID
from src.services.permissions import STAFF_ROLE_IDS, StaffRoleManager

OWNER_ID = 1374053953301385308

TICKET_TYPES = {
    "helper": ("🛡️ Заявка на персонал", "Опишите свой опыт, возраст и почему хотите вступить в персонал."),
    "media": ("🎬 Заявка на медиа", "Пришлите ссылку на канал, статистику и примеры публикаций."),
    "complaint": ("⚠️ Жалоба на игрока", "Укажите ник нарушителя, дату, описание и приложите доказательства."),
    "bug": ("🐛 Сообщение об ошибке", "Опишите шаги воспроизведения ошибки и ожидаемый результат."),
}


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Закрыть и архивировать",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="werigrief_ticket_close"
    )
    async def close_ticket(self, interaction: discord.Interaction, button):
        cog = interaction.client.get_cog("TicketsCog")
        if cog is None:
            await interaction.response.send_message(
                "Система тикетов временно недоступна.", ephemeral=True
            )
            return
        await cog.close_ticket(interaction)


class TicketTypeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Заявка на персонал", value="helper", emoji="🛡️"),
            discord.SelectOption(label="Заявка на медиа", value="media", emoji="🎬"),
            discord.SelectOption(label="Жалоба на игрока", value="complaint", emoji="⚠️"),
            discord.SelectOption(label="Сообщить об ошибке", value="bug", emoji="🐛"),
        ]
        super().__init__(
            placeholder="Выбери тип обращения...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="werigrief_ticket_type"
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("TicketsCog")
        if cog is None:
            await interaction.response.send_message(
                "Система тикетов временно недоступна.", ephemeral=True
            )
            return
        await cog.create_ticket(interaction, self.values[0])


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect())


class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketControlView())

    @commands.hybrid_command(
        name="setup",
        description="Опубликовать панель тикетов WeriGrief"
    )
    @commands.has_guild_permissions(administrator=True)
    async def setup_panel(self, ctx: commands.Context):
        if not ctx.guild:
            await ctx.send("Команда доступна только на сервере.")
            return

        embed = discord.Embed(
            title="🎫 Центр поддержки WeriGrief",
            description=(
                "Добро пожаловать в службу поддержки!\n\n"
                "Выбери категорию обращения в меню ниже.\n"
                "Бот создаст приватный канал, доступный тебе и персоналу.\n\n"
                "🛡️ Заявка на персонал\n"
                "🎬 Заявка на медиа\n"
                "⚠️ Жалоба на игрока\n"
                "🐛 Сообщение об ошибке"
            ),
            color=0x9B59B6
        )
        embed.set_footer(text="WeriGrief • Support Center")
        await ctx.send(embed=embed, view=TicketPanelView())

    async def create_ticket(self, interaction: discord.Interaction, kind: str):
        guild = interaction.guild
        if guild is None or kind not in TICKET_TYPES:
            await interaction.response.send_message(
                "Не удалось определить сервер или категорию.", ephemeral=True
            )
            return

        user = interaction.user
        category = guild.get_channel(SUPPORT_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "Категория тикетов не найдена. Обратись к администрации.",
                ephemeral=True
            )
            return

        for channel in category.text_channels:
            if channel.topic and f"owner={user.id}" in channel.topic and "status=open" in channel.topic:
                await interaction.response.send_message(
                    f"У тебя уже есть открытый тикет: {channel.mention}",
                    ephemeral=True
                )
                return

        await interaction.response.defer(ephemeral=True)

        bot_member = guild.me
        if bot_member is None:
            await interaction.followup.send("Не удалось определить бота на сервере.", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True
            ),
            bot_member: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_channels=True, manage_messages=True, attach_files=True
            ),
        }

        for role_id in STAFF_ROLE_IDS:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True,
                    read_message_history=True, attach_files=True,
                    embed_links=True, manage_messages=True
                )

        label, instructions = TICKET_TYPES[kind]
        safe_name = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]", "", user.name.lower())[:15] or "user"

        try:
            channel = await guild.create_text_channel(
                name=f"{kind}-{safe_name}",
                category=category,
                overwrites=overwrites,
                topic=f"WeriGriefTicket owner={user.id} type={kind} status=open"
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Боту не хватает прав на создание каналов или управление разрешениями.",
                ephemeral=True
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "Discord не смог создать тикет. Попробуй позже.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=label,
            description=(
                f"Здравствуйте, {user.mention}!\n\n"
                f"{instructions}\n\n"
                "Персонал ответит здесь. Не отправляй пароли и токены.\n"
                "Когда вопрос будет решён, персонал или автор может закрыть тикет."
            ),
            color=0x9B59B6
        )
        embed.set_footer(text=f"WeriGrief Support • ID: {user.id}")
        await channel.send(
            content=user.mention,
            embed=embed,
            view=TicketControlView()
        )
        await interaction.followup.send(
            f"✅ Тикет создан: {channel.mention}",
            ephemeral=True
        )

    async def close_ticket(self, interaction: discord.Interaction):
        channel = interaction.channel
        guild = interaction.guild

        if not isinstance(channel, discord.TextChannel) or guild is None:
            await interaction.response.send_message(
                "Эту кнопку можно использовать только внутри тикета.",
                ephemeral=True
            )
            return

        topic = channel.topic or ""
        match = re.search(r"owner=(\d+)", topic)
        if not match or "WeriGriefTicket" not in topic:
            await interaction.response.send_message(
                "Это не тикет WeriGrief.", ephemeral=True
            )
            return

        owner_id = int(match.group(1))
        user = interaction.user
        is_owner = user.id == owner_id
        is_staff = (
            user.guild_permissions.manage_channels
            or user.guild_permissions.administrator
            or any(
                StaffRoleManager.is_staff_role(role)
                for role in getattr(user, "roles", [])
            )
        )

        if not (is_owner or is_staff):
            await interaction.response.send_message(
                "Закрыть тикет может его автор или персонал.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        lines = [
            f"WeriGrief ticket transcript",
            f"Channel: #{channel.name}",
            f"Owner ID: {owner_id}",
            f"Closed by: {user} ({user.id})",
            ""
        ]

        try:
            async for message in channel.history(limit=2000, oldest_first=True):
                content = message.content or ""
                lines.append(
                    f"[{message.created_at.isoformat()}] "
                    f"{message.author} ({message.author.id}): {content}"
                )
                for attachment in message.attachments:
                    lines.append(f"Attachment: {attachment.url}")

            transcript = "\n".join(lines).encode("utf-8")
            file = discord.File(
                io.BytesIO(transcript),
                filename=f"{channel.name}-transcript.txt"
            )
            await channel.send(
                content="📁 Архив переписки тикета:",
                file=file
            )
        except discord.HTTPException:
            pass

        member = guild.get_member(owner_id)
        if member:
            try:
                await channel.set_permissions(
                    member,
                    view_channel=True,
                    send_messages=False,
                    add_reactions=False
                )
            except discord.HTTPException:
                pass

        try:
            await channel.edit(
                name=f"closed-{channel.name}"[:100],
                topic=f"WeriGriefTicket owner={owner_id} status=closed"
            )
        except discord.HTTPException:
            pass

        await interaction.followup.send(
            "🔒 Тикет закрыт и архивирован. История канала сохранена.",
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
