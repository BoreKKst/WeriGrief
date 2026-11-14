"""
Interactive Discord UI Views: support ticket buttons and server rules components.
"""

import re
import asyncio
import discord
from src.core.config import log, GUILD_ID, SUPPORT_CATEGORY_ID, SUPPORT_LINK_GUILD_ID, SUPPORT_LINK_CHANNEL_ID
from src.services.permissions import StaffRoleManager, ROLE_HELPER

SUPPORT_LABEL = "\u2800\u2002Поддержка\u2002\u2800"
STAFF_ROLE_ID = ROLE_HELPER


class TicketControlView(discord.ui.View):
    """Панель управления внутри открытого тикета (кнопка закрытия)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Закрыть тикет", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_close_button")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button | None = None):
        user_roles = [r.id for r in getattr(interaction.user, "roles", [])]
        is_staff = any(StaffRoleManager.is_staff_role(role) for role in getattr(interaction.user, "roles", []))
        is_admin = getattr(interaction.user.guild_permissions, "administrator", False)
        is_author = interaction.channel and str(interaction.user.id) in (interaction.channel.topic or "")

        if not (is_staff or is_admin or is_author):
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Закрыть тикет может только его автор или администрация.", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.send_message("🔒 Тикет будет удалён через 5 секунд...")
        else:
            await interaction.followup.send("🔒 Тикет будет удалён через 5 секунд...")

        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Тикет закрыт пользователем {interaction.user}")
        except Exception as e:
            log.error(f"Failed to delete ticket: {e}")


class CreateTicketConfirmView(discord.ui.View):
    """Подтверждение создания репорт-тикета с предупреждением о ложных репортах."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Подтвердить и создать тикет", style=discord.ButtonStyle.danger, emoji="✉️", custom_id="confirm_create_ticket")
    async def confirm_create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_report_ticket(interaction)


async def create_report_ticket(interaction: discord.Interaction):
    """Создает приватный канал репорт-тикета для пользователя."""
    guild = interaction.guild
    if not guild and interaction.client:
        guild = interaction.client.get_guild(GUILD_ID)

    if not guild:
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Сервер не найден.", ephemeral=True)
        return

    user = interaction.user
    cat = guild.get_channel(SUPPORT_CATEGORY_ID)

    channels_to_check = cat.text_channels if cat else guild.text_channels
    for ch in channels_to_check:
        if ch.topic and f"ID: {user.id}" in ch.topic:
            msg = f"⚠️ У вас уже есть открытый тикет-репорт: {ch.mention}"
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
            return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    staff_role = guild.get_role(STAFF_ROLE_ID)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            read_message_history=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
            read_message_history=True,
        ),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            read_message_history=True,
        )

    clean_nick = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]", "", user.name)
    channel_name = f"репорт-{clean_nick[:15].lower() or 'игрок'}"

    try:
        ticket_ch = await guild.create_text_channel(
            name=channel_name,
            category=cat,
            overwrites=overwrites,
            topic=f"Тикет-репорт от {user} (ID: {user.id})",
        )
    except Exception as e:
        log.error(f"Failed to create ticket channel: {e}")
        await interaction.followup.send("❌ Не удалось создать канал тикета. Обратитесь к администрации сервера.", ephemeral=True)
        return

    embed = discord.Embed(
        title="📩 Тикет для подачи репорта",
        color=0xED4245,
        description=(
            f"Здравствуйте, {user.mention}!\n\n"
            "⚠️ **ПРЕДУПРЕЖДЕНИЕ О ЛОЖНЫХ РЕПОРТАХ:**\n"
            "> В случае **заведомо ложного репорта** (не отклонённого из-за нехватки доказательств, "
            "а намеренно ложного с целью клеветы или обмана) нарушителю выдаётся **мут на 1 день**!\n\n"
            "📋 **Пожалуйста, отправьте в этот чат:**\n"
            "1. **Никнейм нарушителя** (в игре Minecraft или в Discord).\n"
            "2. **Суть нарушения** (опишите, что произошло).\n"
            "3. **Доказательства** (прикрепите скриншоты, видеозапись или логи).\n\n"
            "🛡️ Администрация проекта ответит вам здесь в ближайшее время."
        ),
    )
    embed.set_footer(text="Когда вопрос будет решён, нажмите кнопку ниже")

    staff_ping = f"{staff_role.mention} " if staff_role else ""
    await ticket_ch.send(
        content=f"{user.mention} {staff_ping}",
        embed=embed,
        view=TicketControlView(),
    )

    await interaction.followup.send(
        f"✅ Ваш тикет-репорт успешно создан: {ticket_ch.mention}\nПерейдите в канал и опишите нарушение.",
        ephemeral=True,
    )


class RulesView(discord.ui.View):
    """Интерактивные кнопки под сообщением с правилами сервера."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label=SUPPORT_LABEL,
            url=f"https://discord.com/channels/{SUPPORT_LINK_GUILD_ID}/{SUPPORT_LINK_CHANNEL_ID}",
            style=discord.ButtonStyle.link,
            row=0
        ))

    @discord.ui.button(
        label="Отправка репортов",
        style=discord.ButtonStyle.secondary,
        custom_id="rules_send_report",
        row=0
    )
    async def send_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        warn_embed = discord.Embed(
            title="⚠️ Предупреждение перед подачей репорта",
            color=0xFEE75C,
            description=(
                "Перед созданием тикета обратите внимание:\n\n"
                "🚨 **В случае заведомо ложного репорта** (не отклонённого из-за нехватки доказательств, "
                "а намеренно ложного с целью клеветы или обмана) вам будет выдан **мут на 1 день**!\n\n"
                "Если вы уверены в нарушении и готовы предоставить доказательства, нажмите кнопку ниже для создания приватного тикета."
            ),
        )
        await interaction.response.send_message(
            embed=warn_embed,
            view=CreateTicketConfirmView(),
            ephemeral=True,
        )

