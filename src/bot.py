"""
Entry point for WeriGrief Staff Management Bot.
Initializes bot, registers services, cogs, UI components and event listeners.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands

# ─── Импорт конфигурации и утилит ──────────────────────────
from src.core.config import (
    TOKEN, GUILD_ID, SPREADSHEET_ID, GOOGLE_CREDS_FILE, log
)
from src.core.database import init_db, close_db
from src.services.staff_resolver import StaffResolver
from src.services.playtime import PlaytimeTracker
from src.services.sheets_monitor import SheetsSyncTask
from src.services.automod import AutomodService
from src.ui.views import (
    RulesView, CreateTicketConfirmView, TicketControlView,
    create_report_ticket
)
from src.cogs.warnings import WarningsCog
from src.cogs.staff import StaffCog
from src.cogs.norm import NormCog
from src.modules.economy.cog import EconomyCog
from src.modules.pets.cog import PetsCog
from src.modules.tickets.cog import TicketsCog
from src.services.sheets_api import SheetsMonitor


# ─── Инициализация бота ────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Глобальные сервисы
sheets_monitor: SheetsMonitor | None = None
staff_resolver: StaffResolver | None = None
playtime_tracker: PlaytimeTracker | None = None
sheets_sync_task: SheetsSyncTask | None = None
automod_service: AutomodService | None = None
_initialized = False


@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Глобальный обработчик интерактивных кнопок интерфейса."""
    if interaction.type == discord.InteractionType.component:
        cid = interaction.data.get("custom_id")
        if cid == "rules_send_report":
            if not interaction.response.is_done():
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
            return
        elif cid == "confirm_create_ticket":
            await create_report_ticket(interaction)
            return
        elif cid == "ticket_close_button":
            view = TicketControlView()
            await view.close_ticket(interaction, None)
            return


@bot.event
async def on_message(message: discord.Message):
    """Фильтрация сторонних ссылок от обычных игроков и обработка текстовых команд."""
    if not message.guild or not isinstance(message.author, discord.Member):
        await bot.process_commands(message)
        return

    if automod_service and not automod_service.is_link_filter_exempt(message.author, message.channel):
        if automod_service.contains_forbidden_link(message.content, message.guild.id):
            try:
                await message.delete()
                log.info(
                    f"[Automod] Deleted link from player {message.author} ({message.author.id}) in #{message.channel.name}: {message.content[:100]}"
                )
                await message.channel.send(
                    f"⚠️ {message.author.mention}, отправка сторонних ссылок запрещена правилами сервера!",
                    delete_after=5,
                )
                return
            except discord.Forbidden:
                log.warning(f"[Automod] Missing permissions to delete message {message.id} in #{message.channel.name}")
            except discord.HTTPException as e:
                log.error(f"[Automod] Failed to delete message {message.id}: {e}")

    await bot.process_commands(message)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    """Фильтрация ссылок при редактировании сообщений."""
    if not after.guild or not isinstance(after.author, discord.Member):
        return

    if automod_service and not automod_service.is_link_filter_exempt(after.author, after.channel):
        if automod_service.contains_forbidden_link(after.content, after.guild.id):
            try:
                await after.delete()
                log.info(
                    f"[Automod] Deleted edited link from player {after.author} ({after.author.id}) in #{after.channel.name}: {after.content[:100]}"
                )
                await after.channel.send(
                    f"⚠️ {after.author.mention}, отправка сторонних ссылок запрещена правилами сервера!",
                    delete_after=5,
                )
            except discord.Forbidden:
                log.warning(f"[Automod] Missing permissions to delete edited message {after.id} in #{after.channel.name}")
            except discord.HTTPException as e:
                log.error(f"[Automod] Failed to delete edited message {after.id}: {e}")


@bot.event
async def on_ready():
    """Событие готовности бота: регистрация постоянных Views, слэш-команд и запуск циклов."""
    global sheets_monitor, staff_resolver, playtime_tracker, sheets_sync_task, automod_service, _initialized
    if _initialized:
        return

    # Регистрация UI Views для постоянной обработки кнопок после перезапуска
    bot.add_view(RulesView())
    bot.add_view(CreateTicketConfirmView())
    bot.add_view(TicketControlView())

    guild = bot.get_guild(GUILD_ID)
    if guild:
        log.info(f"Guild: {guild.name} (members: {guild.member_count})")
        if staff_resolver:
            await staff_resolver.resolve_discord_ids(guild)

    else:
        log.warning(f"Guild {GUILD_ID} not found!")

    # Инициализация SheetsMonitor
    try:
        sheets_monitor = SheetsMonitor(GOOGLE_CREDS_FILE, SPREADSHEET_ID)
        log.info("Google Sheets monitor initialized (Read/Write mode)")

        # Инициализация сервисов
        staff_resolver = StaffResolver(sheets_monitor)
        staff_resolver.populate_sheets_discord_map()

        playtime_tracker = PlaytimeTracker(sheets_monitor, staff_resolver)
        automod_service = AutomodService(staff_resolver)

        # Запуск Minecraft Query + PostgreSQL трекера
        await playtime_tracker.start_query_tracking()

        # Подключение Cogs
        await bot.add_cog(WarningsCog(bot, sheets_monitor, staff_resolver))
        await bot.add_cog(StaffCog(bot, sheets_monitor, staff_resolver))
        await bot.add_cog(NormCog(bot, sheets_monitor, staff_resolver, playtime_tracker))
        await bot.add_cog(EconomyCog(bot))
        await bot.add_cog(PetsCog(bot))
        await bot.add_cog(TicketsCog(bot))

        # Одноразовое публичное объявление об обновлении
        from src.modules.announcements import publish_update_announcement
        await publish_update_announcement(bot)


        # Синхронизация slash-команд после загрузки Cogs
        if guild:
            try:
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                synced_names = sorted(command.name for command in synced)
                log.info(f"✅ Synced {len(synced)} slash commands to guild '{guild.name}': {', '.join(synced_names)}")
                if "unstaff" not in synced_names:
                    log.error("/unstaff was not returned by Discord after command sync")
            except Exception as e:
                log.error(f"Failed to sync slash commands: {e}")

        # Запуск фонового опроса таблиц
        sheets_sync_task = SheetsSyncTask(bot, sheets_monitor, staff_resolver, playtime_tracker)
        sheets_sync_task.start()
        _initialized = True

    except Exception as e:
        log.error(f"Failed to init Sheets monitor or services: {e}", exc_info=True)


async def run_bot():
    """Запускает Discord-бота с PostgreSQL."""
    await init_db()
    try:
        async with bot:
            await bot.start(TOKEN)
    finally:
        # Сначала останавливаем фоновые задачи, пока PostgreSQL доступен.
        if sheets_sync_task:
            try:
                await sheets_sync_task.stop()
                log.info("Sheets background tasks stopped")
            except Exception:
                log.exception("Failed to stop Sheets background tasks")

        if playtime_tracker:
            try:
                await playtime_tracker.stop_query_tracking()
                log.info("Minecraft Query tracker stopped")
            except Exception:
                log.exception("Failed to stop Minecraft Query tracker")

        await close_db()



def main():
    """Точка входа запуска бота."""
    if not TOKEN:
        log.error("BOT_TOKEN not set in environment!")
        return

    log.info("Starting WeriGrief Staff Management Bot...")
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
