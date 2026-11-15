import logging
import discord

from src.core.database import get_pool

log = logging.getLogger("werigrief.announcements")

NEWS_CHANNEL_ID = 1554542427442909256
ANNOUNCEMENT_KEY = "werigrief_public_update_2026"

def commands_embed():
    embed = discord.Embed(
        title="📖 Команды WeriGrief",
        description="Основные команды игрового бота:",
        color=0x9B59B6,
    )
    embed.add_field(
        name="💎 Экономика",
        value=(
            "`/balance` — баланс Риликов\n"
            "`/daily` — ежедневная награда\n"
            "`/bonus` — бонус\n"
            "`/salary` — недельная зарплата\n"
            "`/work` — работа и заработок\n"
            "`/pay` — перевод Риликов\n"
            "`/top` — рейтинг\n"
            "`/profile` — профиль\n"
            "`/shop` — магазин VIP\n"
            "`/give` — выдача Риликов владельцем\n"
            "`/list` — список участников"
        ),
        inline=False,
    )
    embed.add_field(
        name="🐾 Питомцы",
        value=(
            "`/pets` — меню питомцев\n"
            "`/petshop` — магазин питомцев и еды"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎫 Поддержка",
        value="`/setup` — публикация панели тикетов (только администратор)",
        inline=False,
    )
    embed.set_footer(text="WeriGrief • Список игровых команд")
    return embed


def tutorial_embed():
    embed = discord.Embed(
        title="❔ Быстрый старт WeriGrief",
        description=(
            "**1.** Напиши `/daily`, чтобы получить ежедневную награду.\n\n"
            "**2.** Используй `/work`, чтобы зарабатывать Рилики.\n\n"
            "**3.** Проверь деньги командой `/balance`.\n\n"
            "**4.** Открой `/petshop`, чтобы приобрести питомца.\n\n"
            "**5.** Используй `/pets`, чтобы управлять питомцами.\n\n"
            "**6.** Открой `/shop`, чтобы посмотреть VIP-магазин.\n\n"
            "Для перевода валюты другому участнику используй `/pay`."
        ),
        color=0x9B59B6,
    )
    embed.set_footer(text="WeriGrief • Начни своё приключение!")
    return embed


class UpdateAnnouncementView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Команды",
        emoji="📖",
        style=discord.ButtonStyle.primary,
        custom_id="werigrief_update_commands_v1",
    )
    async def commands_button(self, interaction, button):
        await interaction.response.send_message(
            embed=commands_embed(),
            ephemeral=True,
            delete_after=60,
        )

    @discord.ui.button(
        label="Как пользоваться",
        emoji="❔",
        style=discord.ButtonStyle.secondary,
        custom_id="werigrief_update_tutorial_v1",
    )
    async def tutorial_button(self, interaction, button):
        await interaction.response.send_message(
            embed=tutorial_embed(),
            ephemeral=True,
            delete_after=60,
        )


def announcement_embed():
    embed = discord.Embed(
        title="🌙 WeriGrief — обновление!",
        description=(
            "Мы подготовили новые возможности для участников сервера!\n\n"
            "💎 **Экономика** — Рилики, работа, награды, переводы и рейтинги.\n"
            "🐾 **WeriPets** — питомцы, уход и магазин.\n"
            "🛒 **VIP-магазин** — покупка привилегии.\n"
            "🎫 **Поддержка** — удобная система тикетов.\n\n"
            "Нажми на кнопки ниже, чтобы посмотреть команды "
            "и узнать, с чего начать!"
        ),
        color=0x9B59B6,
    )
    embed.set_footer(text="WeriGrief • Спасибо, что играете с нами 🌙")
    return embed


async def publish_update_announcement(bot):
    pool = get_pool()

    async with pool.acquire() as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS system")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS system.announcement_state (
                announcement_key TEXT PRIMARY KEY,
                message_id BIGINT,
                status TEXT NOT NULL DEFAULT 'sending',
                claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                sent_at TIMESTAMPTZ
            )
        """)
        await conn.execute("""
            ALTER TABLE system.announcement_state
            ALTER COLUMN message_id DROP NOT NULL
        """)
        await conn.execute("""
            ALTER TABLE system.announcement_state
            ALTER COLUMN sent_at DROP NOT NULL
        """)
        await conn.execute("""
            ALTER TABLE system.announcement_state
            ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'sent'
        """)
        await conn.execute("""
            ALTER TABLE system.announcement_state
            ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        """)
        await conn.execute("""
            ALTER TABLE system.announcement_state
            ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ
        """)

    bot.add_view(UpdateAnnouncementView())

    try:
        channel = bot.get_channel(NEWS_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(NEWS_CHANNEL_ID)

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1)",
                    2026111501,
                )

                existing = await conn.fetchrow(
                    """
                    SELECT status, message_id
                    FROM system.announcement_state
                    WHERE announcement_key = $1
                    """,
                    ANNOUNCEMENT_KEY,
                )

                if existing:
                    log.info(
                        "Announcement not repeated; status=%s, message_id=%s",
                        existing["status"],
                        existing["message_id"],
                    )
                    return

                await conn.execute(
                    """
                    INSERT INTO system.announcement_state
                        (announcement_key, status)
                    VALUES ($1, 'sending')
                    """,
                    ANNOUNCEMENT_KEY,
                )

        message = await channel.send(
            embed=announcement_embed(),
            view=UpdateAnnouncementView(),
        )

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE system.announcement_state
                SET status = 'sent',
                    message_id = $2,
                    sent_at = NOW()
                WHERE announcement_key = $1
                """,
                ANNOUNCEMENT_KEY,
                message.id,
            )

        log.info("Public update announcement sent: %s", message.id)

    except Exception:
        log.exception(
            "Could not publish announcement. Check PostgreSQL status before retrying."
        )
