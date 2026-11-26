import asyncio
import sys
import discord
from discord.ext import commands

from src.core.config import (
    TOKEN, GUILD_ID, SPREADSHEET_ID, GOOGLE_CREDS_FILE, log
)
from src.core.database import init_db, close_db
from src.services.staff_resolver import StaffResolver
from src.services.playtime import PlaytimeTracker
from src.services.sheets_monitor import SheetsSyncTask
from src.services.sheets_api import SheetsMonitor
from src.services.permissions import StaffRoleManager, STAFF_ROLE_IDS
from src.cogs.warnings import WarningsCog
from src.cogs.staff import StaffCog
from src.cogs.norm import NormCog

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

sheets_monitor = None
staff_resolver = None
playtime_tracker = None
sheets_sync_task = None
_initialized = False

CATEGORY_RENAMES = {
    "------『инɸоᴩʍᴀция』--------": "📚・ИНФОРМАЦИЯ",
    "------「  ᴦᴧᴀʙный ᴄᴇᴋᴛоᴩ  」--------": "💬・ОБЩЕНИЕ",
    "------『ɯᴛᴀб』--------": "🛡️・ПЕРСОНАЛ",
    "-----『ᴨᴩиʙᴀᴛᴋи』-------": "🔐・ЛИЧНЫЕ КОМНАТЫ",
    "-----『ᴨоддᴇᴩжᴋᴀ』------": "🛟・ПОДДЕРЖКА",
    "-----『нᴀбоᴩы』-------": "🎟️・НАБОРЫ",
}
CHANNEL_RENAMES = {
    "『🔰』ᴨᴩоходной-дʙоᴩ": "👋・вход",
    "『📋』ᴨᴩᴀʙиᴧы-диᴄᴋоᴩд-ᴄᴇᴩʙᴇᴩᴀ": "📜・правила-дискорда",
    "『🔮』ноʙоᴄᴛи": "📢・новости",
    "『💼』ᴨᴀᴩᴛнᴇᴩы": "🤝・партнёры",
    "『🔔』оᴨоʙᴇщᴇния": "🔔・уведомления",
    "『📃』ᴄоᴛᴩудничᴇᴄᴛʙо": "📝・сотрудничество",
    "『📋』ᴨᴩᴀʙиᴧы-ᴄᴇᴩʙᴇᴩᴀ": "📜・правила-сервера",
    "『🟫』ресурспак": "🧩・ресурспак",
    "IP: werigrief.nexuz.ru": "🌐・ip-сервера",
    "「🎙️」общᴇниᴇ": "🎙️・голосовой-общий",
    "「🎙️」общᴇниᴇ 2": "🎙️・голосовой-2",
    "「🎙️」общᴇниᴇ 3": "🎙️・голосовой-3",
    "『💬』общий": "💬・общий-чат",
    "『🪄』ᴛʙоᴩчᴇᴄᴛʙо": "🎨・творчество",
    "『📸』ᴍᴇᴅɪᴀ-ᴄʜᴀᴛ": "📸・медиа-чат",
    "『🗿』мемы": "🤣・мемы",
    "『🗑』корзина": "🗑️・корзина",
    "「🎙️」ᴦоᴧоᴄоʙой чᴀᴛ": "🎙️・голосовой-персонал",
    "「🔊 」ᴄобᴩᴀниᴇ": "🔊・собрание",
    "『📋』зᴀдᴀчи": "📋・задачи",
    "『🚫』ᴧоᴦи": "📜・логи",
    "『🚫』нᴀᴩуɯᴇниᴇ": "⚠️・нарушения",
    "『🔄』ᴨᴇᴩᴄонᴀᴧ": "🛡️・персонал",
    "『📜』ᴀдʍиниᴄᴛᴩᴀция": "⚖️・администрация",
    "『🔒』доверенный-чат": "🔒・доверенный-чат",
    "「🎙️」доверенный голосовой чат": "🎙️・доверенный-голосовой",
    "『📄』ʍᴀнуᴀᴧ-дᴧя-ᴨᴩоʙᴇᴩоᴋ": "📘・мануал-проверок",
    "『🔒』staff-chat": "🔒・staff-chat",
    "「👤」Собеседование": "🗣️・собеседование",
    "『🔲』ᴨᴩᴀʙиᴧы-ᴨᴇᴩᴄонᴀᴧᴀ": "📜・правила-персонала",
    "『📈』система-повышений": "📈・повышения",
    "『🛠️』бᴀᴦи": "🐛・старые-баги",
    "『🚨』жᴀᴧобы": "🚨・старые-жалобы",
    "『⚙️』уᴨᴩᴀʙᴧᴇниᴇ": "⚙️・управление-комнатами",
    "「➕ 」  ᴄоздᴀᴛь": "🎙️・создать-комнату-alt",
    "『🧩』команды-бота": "🤖・команды-бота",
}
CHANNEL_ID_RENAMES = {
    1554538230714466315: "📜・правила-дискорда",
    1572902563290939452: "📜・правила-сервера",
    1567562924439113938: "🎉・розыгрыши",
    1569961265416372305: "📜・правила-персонала",
}
ROLE_RENAMES = {
    "Партнёр": ("🤝・Партнёр", 0xF1C40F),
    "• Тикток": ("🎬・TikTok", 0xE91E63),
    "【 ⚠️ 】На испытании": ("⚠️・На испытании", 0xF39C12),
    "Кандидат": ("📝・Кандидат", 0x3498DB),
    "Поддержка": ("🎧・Поддержка", 0x1ABC9C),
    "Подписка": ("⭐・Подписка", 0x9B59B6),
    "• раб латекса": ("🧰・раб латекса", 0x7F8C8D),
    "• BOY": ("♂️・Мужской", 0x3498DB),
    "• GIRL": ("♀️・Женский", 0xE84393),
}


async def ensure_server_aesthetics(guild: discord.Guild):
    """Apply readable names, consistent emoji labels, and colors to existing server items."""
    for category in guild.categories:
        new_name = CATEGORY_RENAMES.get(category.name)
        if new_name and category.name != new_name:
            await category.edit(name=new_name, reason="Единое оформление категорий WeriGrief")
    for channel in list(guild.channels):
        new_name = CHANNEL_ID_RENAMES.get(channel.id, CHANNEL_RENAMES.get(channel.name))
        if new_name and channel.name != new_name:
            await channel.edit(name=new_name, reason="Единое оформление каналов WeriGrief")
    for role in guild.roles:
        style = ROLE_RENAMES.get(role.name)
        if style and not role.managed:
            new_name, color = style
            await role.edit(name=new_name, colour=discord.Colour(color), reason="Единое оформление ролей WeriGrief")


async def ensure_staff_area_access(guild: discord.Guild):
    """Restore staff visibility after role renames and grant access to staff rooms."""
    category = discord.utils.get(guild.categories, name="🛡️・ПЕРСОНАЛ")
    if category is None:
        return
    staff_roles = [role for role in guild.roles if role.id in STAFF_ROLE_IDS]
    category_overwrites = dict(category.overwrites)
    for role in staff_roles:
        category_overwrites[role] = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True,
            send_messages=True, attach_files=True, embed_links=True,
            connect=True, speak=True,
        )
    await category.edit(overwrites=category_overwrites, reason="Доступ ролей персонала к закрытой категории")

    read_only = {"📜・логи", "⚠️・нарушения", "⚖️・администрация", "📘・мануал-проверок", "📜・правила-персонала"}
    for channel in list(category.channels):
        overwrites = dict(channel.overwrites)
        for role in staff_roles:
            can_write = channel.name not in read_only
            values = {
                "view_channel": True,
                "read_message_history": True,
                "send_messages": can_write if isinstance(channel, discord.TextChannel) else None,
                "attach_files": can_write if isinstance(channel, discord.TextChannel) else None,
                "embed_links": can_write if isinstance(channel, discord.TextChannel) else None,
                "connect": True if isinstance(channel, discord.VoiceChannel) else None,
                "speak": True if isinstance(channel, discord.VoiceChannel) else None,
            }
            overwrites[role] = discord.PermissionOverwrite(**values)
        await channel.edit(overwrites=overwrites, reason="Доступ сотрудников к каналам персонала")


async def ensure_operational_channels(guild: discord.Guild):
    """Find or create private staff log channels and refresh imported config IDs."""
    import src.core.config as config

    def resolve(channel_id: int, names: tuple[str, ...]):
        channel = guild.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            return channel
        return next((ch for ch in guild.text_channels if ch.name.casefold() in names), None)

    staff_channel = resolve(config.STAFF_NOTIFY_CHANNEL, ("📋・персонал", "🛡️・персонал", "🛡️・персонал", "персонал"))
    warn_channel = resolve(config.WARN_NOTIFY_CHANNEL, ("⚠️・предупреждения", "⚠️・нарушения", "предупреждения", "логи-варнов"))
    category = discord.utils.get(guild.categories, name="🛡️・WeriTime журналы")
    needs_category = category is None and (
        not staff_channel or not warn_channel
        or (staff_channel and staff_channel.category is None)
        or (warn_channel and warn_channel.category is None)
    )
    if staff_channel and warn_channel and not needs_category:
        staff_id, warn_id = staff_channel.id, warn_channel.id
    else:
        bot_member = guild.me
        if not bot_member or not bot_member.guild_permissions.manage_channels:
            log.warning("Missing WeriTime log channels; bot needs Manage Channels to create them")
            return
        if category is None:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                bot_member: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True,
                    embed_links=True, attach_files=True,
                ),
            }
            for role in guild.roles:
                if role.id in STAFF_ROLE_IDS:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True,
                    )
            category = await guild.create_category(
                "🛡️・WeriTime журналы",
                overwrites=overwrites,
                reason="Создание закрытых служебных журналов WeriTime",
            )
        for existing_channel in (staff_channel, warn_channel):
            if existing_channel and existing_channel.category is None:
                await existing_channel.edit(category=category, reason="Организация служебных журналов WeriTime")
        staff_id = staff_channel.id if staff_channel else 0
        warn_id = warn_channel.id if warn_channel else 0
        if not staff_channel:
            staff_channel = await guild.create_text_channel(
                "📋・персонал", category=category, overwrites=category.overwrites,
                topic="Закрытый журнал назначений, переводов и изменений персонала WeriTime",
                reason="Создание журнала персонала WeriTime",
            )
            staff_id = staff_channel.id
        if not warn_channel:
            warn_channel = await guild.create_text_channel(
                "⚠️・предупреждения", category=category, overwrites=category.overwrites,
                topic="Закрытый журнал предупреждений и действий автомодерации WeriTime",
                reason="Создание журнала предупреждений WeriTime",
            )
            warn_id = warn_channel.id

    config.STAFF_NOTIFY_CHANNEL = staff_id
    config.WARN_NOTIFY_CHANNEL = warn_id
    for module_name in (
        "src.cogs.staff", "src.cogs.norm", "src.cogs.automod", "src.cogs.warnings",
        "src.services.playtime", "src.services.auto_dismissal", "src.services.sheets_monitor",
    ):
        module = sys.modules.get(module_name)
        if module:
            if hasattr(module, "STAFF_NOTIFY_CHANNEL"):
                module.STAFF_NOTIFY_CHANNEL = staff_id
            if hasattr(module, "WARN_NOTIFY_CHANNEL"):
                module.WARN_NOTIFY_CHANNEL = warn_id
    log.info("WeriTime log channels ready: staff=%s warnings=%s", staff_id, warn_id)


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    if channel.guild.id != GUILD_ID:
        return
    import src.core.config as config
    if channel.id in {config.STAFF_NOTIFY_CHANNEL, config.WARN_NOTIFY_CHANNEL} or channel.name == "🛡️・WeriTime журналы":
        try:
            await ensure_operational_channels(channel.guild)
        except discord.HTTPException:
            log.exception("Could not restore a deleted WeriTime log channel")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    content = message.content.casefold()
    creator_phrases = ("кто создатель", "кто создал", "кто автор", "кто сделал weritime")
    if any(phrase in content for phrase in creator_phrases):
        await message.reply("Создатель WeriTime — BoreKKst.", mention_author=False)
    await bot.process_commands(message)


@bot.event
async def on_ready():
    global sheets_monitor, staff_resolver, playtime_tracker
    global sheets_sync_task, _initialized

    if _initialized:
        return

    guild = bot.get_guild(GUILD_ID)

    if guild:
        log.info("Guild: %s (members: %s)", guild.name, guild.member_count)
    else:
        log.warning("Guild %s not found!", GUILD_ID)

    try:
        if guild:
            await StaffRoleManager.ensure_staff_roles(guild)
            try:
                await ensure_operational_channels(guild)
                await ensure_server_aesthetics(guild)
                await ensure_staff_area_access(guild)
            except discord.HTTPException:
                log.exception("Could not create missing WeriTime log channels")

        sheets_monitor = SheetsMonitor(
            GOOGLE_CREDS_FILE,
            SPREADSHEET_ID
        )

        staff_resolver = StaffResolver(sheets_monitor)
        staff_resolver.populate_sheets_discord_map()

        # На каждом запуске приводим Discord-роли в соответствие с текущим
        # официальным составом Google Sheets, включая уже существующих сотрудников.
        if guild:
            for entry in sheets_monitor.get_staff_list():
                nick = (entry.get("nick") or "").strip()
                position = (entry.get("position") or "Сотрудник").strip()
                if not nick:
                    continue
                member = staff_resolver.find_member_by_mc_nick(guild, nick)
                if member:
                    granted, blocked = await StaffRoleManager.grant_discord_staff_roles(
                        member,
                        position=position,
                        reason="Сверка ролей с Google Sheets при запуске WeriTime",
                    )
                    if granted or blocked:
                        log.info("Startup role sync for %s: granted=%s blocked=%s", nick, granted, blocked)

        playtime_tracker = PlaytimeTracker(
            sheets_monitor,
            staff_resolver
        )

        await playtime_tracker.start_query_tracking()

        await bot.add_cog(
            WarningsCog(bot, sheets_monitor, staff_resolver)
        )
        await bot.add_cog(
            StaffCog(bot, sheets_monitor, staff_resolver)
        )
        await bot.add_cog(
            NormCog(
                bot,
                sheets_monitor,
                staff_resolver,
                playtime_tracker
            )
        )

        from src.cogs.tickets import TicketsCog
        await bot.add_cog(TicketsCog(bot))
        from src.cogs.community import CommunityCog
        await bot.add_cog(CommunityCog(bot))


        from src.modules.voice_join.cog import VoiceJoinCog
        await bot.add_cog(VoiceJoinCog(bot))
        from src.modules.store.rooms import PersonalRoomsCog
        await bot.add_cog(PersonalRoomsCog(bot))

        from src.cogs.automod import AutoModerationCog
        await bot.add_cog(AutoModerationCog(bot))
        from src.cogs.ai_assistant import AIAssistantCog
        await bot.add_cog(AIAssistantCog(bot))

        if guild:
            try:
                bot.tree.clear_commands(guild=guild)
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                names = sorted(command.name for command in synced)
                log.info(
                    "Synced %s commands: %s",
                    len(names),
                    ", ".join(names)
                )
            except Exception:
                log.exception("Slash command sync failed")

        sheets_sync_task = SheetsSyncTask(
            bot,
            sheets_monitor,
            staff_resolver,
            playtime_tracker
        )
        sheets_sync_task.start()

        _initialized = True
        log.info("WeriTime Lite started successfully")

    except Exception:
        log.exception("Failed to initialize WeriTime Lite services")


async def run_bot():
    await init_db()

    try:
        async with bot:
            await bot.start(TOKEN)
    finally:
        if sheets_sync_task:
            try:
                await sheets_sync_task.stop()
            except Exception:
                log.exception("Failed to stop Sheets task")

        if playtime_tracker:
            try:
                await playtime_tracker.stop_query_tracking()
            except Exception:
                log.exception("Failed to stop Query tracker")

        await close_db()


def main():
    if not TOKEN:
        log.error("BOT_TOKEN not set!")
        return

    log.info("Starting WeriTime Lite...")
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
