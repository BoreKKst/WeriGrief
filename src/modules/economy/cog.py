"""
WeriGrief Economy
PostgreSQL / asyncpg edition.
"""

from __future__ import annotations

import random
import time

import discord
from discord.ext import commands

from src.core.database import get_pool
from src.modules.store.ui import ShopView


OWNER_ID = 1374053953301385308

DAILY_REWARD = 2500
BONUS_REWARD = 555
SALARY_REWARD = 1200

DAILY_COOLDOWN = 24 * 60 * 60
WORK_COOLDOWN = 30 * 60
SALARY_COOLDOWN = 7 * 24 * 60 * 60

EMBED_COLOR = 0x9B59B6


class EconomyDatabase:
    """Асинхронный доступ к PostgreSQL."""

    async def ensure_wallet(self, conn, user_id: int):
        await conn.execute(
            """
            INSERT INTO economy.wallets (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )

    async def get_wallet(self, user_id: int):
        pool = get_pool()
        async with pool.acquire() as conn:
            await self.ensure_wallet(conn, user_id)
            return await conn.fetchrow(
                "SELECT * FROM economy.wallets WHERE user_id = $1",
                user_id,
            )


class EconomyCog(commands.Cog):
    """Основные команды экономики WeriGrief."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database = EconomyDatabase()

    @staticmethod
    def make_embed(
        title: str,
        description: str,
        color: int = EMBED_COLOR,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="WeriGrief • Экономика")
        return embed

    @staticmethod
    def format_time(seconds: float) -> str:
        seconds = max(0, int(seconds))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours:
            return f"{hours} ч. {minutes} мин."
        if minutes:
            return f"{minutes} мин. {seconds} сек."
        return f"{seconds} сек."

    async def claim_reward(
        self,
        ctx: commands.Context,
        action: str,
        amount: int,
        cooldown: int,
        title: str,
    ):
        user_id = ctx.author.id
        now = time.time()
        pool = get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await self.database.ensure_wallet(conn, user_id)

                # Блокировка кошелька защищает от двойного получения.
                await conn.fetchrow(
                    """
                    SELECT user_id FROM economy.wallets
                    WHERE user_id = $1 FOR UPDATE
                    """,
                    user_id,
                )

                expires = await conn.fetchval(
                    """
                    SELECT expires_at FROM economy.cooldowns
                    WHERE user_id = $1 AND action = $2
                    """,
                    user_id,
                    action,
                )

                if expires and expires > now:
                    remaining = self.format_time(expires - now)
                    result = ("wait", remaining)
                else:
                    await conn.execute(
                        """
                        INSERT INTO economy.cooldowns
                            (user_id, action, expires_at)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, action)
                        DO UPDATE SET expires_at = EXCLUDED.expires_at
                        """,
                        user_id,
                        action,
                        now + cooldown,
                    )

                    await conn.execute(
                        """
                        UPDATE economy.wallets
                        SET balance = balance + $1,
                            total_earned = total_earned + $1
                        WHERE user_id = $2
                        """,
                        amount,
                        user_id,
                    )

                    await conn.execute(
                        """
                        INSERT INTO economy.transactions
                            (receiver_id, amount, reason, created_at)
                        VALUES ($1, $2, $3, $4)
                        """,
                        user_id,
                        amount,
                        action,
                        now,
                    )
                    result = ("success", None)

        if result[0] == "wait":
            await ctx.send(embed=self.make_embed(
                "⏳ Награда пока недоступна",
                f"Следующая награда будет доступна через **{result[1]}**.",
                0xE67E22,
            ))
            return

        await ctx.send(embed=self.make_embed(
            title,
            f"{ctx.author.mention}, награда зачислена!\n\n"
            f"**+{amount:,} Риликов**",
            0x2ECC71,
        ))

    @commands.hybrid_command(
        name="balance",
        aliases=["баланс"],
        description="Посмотреть баланс Риликов",
    )
    async def balance(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ):
        member = member or ctx.author
        wallet = await self.database.get_wallet(member.id)

        await ctx.send(embed=self.make_embed(
            "💎 Кошелёк",
            f"**Владелец:** {member.mention}\n\n"
            f"**Баланс:** {wallet['balance']:,} Риликов",
        ))

    @commands.hybrid_command(
        name="daily",
        description="Получить ежедневную награду",
    )
    async def daily(self, ctx: commands.Context):
        await self.claim_reward(
            ctx, "daily", DAILY_REWARD,
            DAILY_COOLDOWN, "🌙 Ежедневная награда",
        )

    @commands.hybrid_command(
        name="bonus",
        description="Получить бонус",
    )
    async def bonus(self, ctx: commands.Context):
        await self.claim_reward(
            ctx, "bonus", BONUS_REWARD,
            DAILY_COOLDOWN, "🎁 Бонус получен",
        )

    @commands.hybrid_command(
        name="salary",
        aliases=["зарплата"],
        description="Получить недельную зарплату",
    )
    async def salary(self, ctx: commands.Context):
        await self.claim_reward(
            ctx, "salary", SALARY_REWARD,
            SALARY_COOLDOWN, "💼 Недельная зарплата",
        )

    @commands.hybrid_command(
        name="work",
        description="Отправиться на работу",
    )
    async def work(self, ctx: commands.Context):
        user_id = ctx.author.id
        now = time.time()

        jobs = [
            ("⛏️ Шахтёр", "Ты добыл ценные кристаллы."),
            ("🔨 Кузнец", "Ты изготовил новые инструменты."),
            ("🌲 Лесоруб", "Ты заготовил древесину."),
            ("🧪 Алхимик", "Ты приготовил редкое зелье."),
            ("🏗️ Строитель", "Ты завершил важный заказ."),
            ("💎 Ювелир", "Ты обработал драгоценные камни."),
        ]

        job, description = random.choice(jobs)
        reward = random.randint(100, 800)
        gained_xp = random.randint(15, 35)
        level_ups = 0
        pool = get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await self.database.ensure_wallet(conn, user_id)

                wallet = await conn.fetchrow(
                    """
                    SELECT * FROM economy.wallets
                    WHERE user_id = $1 FOR UPDATE
                    """,
                    user_id,
                )

                expires = await conn.fetchval(
                    """
                    SELECT expires_at FROM economy.cooldowns
                    WHERE user_id = $1 AND action = 'work'
                    """,
                    user_id,
                )

                if expires and expires > now:
                    result = ("wait", self.format_time(expires - now))
                else:
                    await conn.execute(
                        """
                        INSERT INTO economy.cooldowns
                            (user_id, action, expires_at)
                        VALUES ($1, 'work', $2)
                        ON CONFLICT (user_id, action)
                        DO UPDATE SET expires_at = EXCLUDED.expires_at
                        """,
                        user_id,
                        now + WORK_COOLDOWN,
                    )

                    xp = wallet["work_xp"] + gained_xp
                    level = wallet["work_level"]

                    while xp >= level * 100:
                        xp -= level * 100
                        level += 1
                        level_ups += 1

                    total_reward = reward + level_ups * 500

                    await conn.execute(
                        """
                        UPDATE economy.wallets
                        SET balance = balance + $1,
                            total_earned = total_earned + $1,
                            work_xp = $2,
                            work_level = $3,
                            work_count = work_count + 1
                        WHERE user_id = $4
                        """,
                        total_reward,
                        xp,
                        level,
                        user_id,
                    )

                    await conn.execute(
                        """
                        INSERT INTO economy.transactions
                            (receiver_id, amount, reason, created_at)
                        VALUES ($1, $2, 'work', $3)
                        """,
                        user_id,
                        total_reward,
                        now,
                    )
                    result = ("success", level)

        if result[0] == "wait":
            await ctx.send(embed=self.make_embed(
                "🛠️ Перерыв",
                f"Ты уже работал. Отдохни ещё **{result[1]}**.",
                0xE67E22,
            ))
            return

        embed = self.make_embed(
            "💼 Рабочая смена завершена",
            f"**Профессия:** {job}\n"
            f"{description}\n\n"
            f"💎 **Заработано:** +{reward + level_ups * 500:,} Риликов\n"
            f"⭐ **Опыт профессии:** +{gained_xp} XP\n"
            f"📈 **Уровень профессии:** {level}",
            0x2ECC71,
        )

        if level_ups:
            embed.add_field(
                name="🎉 Повышение!",
                value=f"Новых уровней: **{level_ups}**\n"
                      f"Бонус за уровни: **{level_ups * 500:,} Риликов**",
                inline=False,
            )

        await ctx.send(embed=embed)

        if level_ups:
            try:
                await ctx.author.send(
                    f"🎉 Поздравляем! Твоя профессия в WeriGrief "
                    f"достигла **{level} уровня**!"
                )
            except discord.Forbidden:
                pass

    @commands.hybrid_command(
        name="pay",
        description="Перевести Рилики другому участнику",
    )
    async def pay(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
    ):
        if member.bot or member.id == ctx.author.id:
            await ctx.send("❌ Нельзя переводить Рилики этому участнику.")
            return

        if amount <= 0 or amount > 1_000_000_000:
            await ctx.send("❌ Укажи корректную сумму перевода.")
            return

        sender_id = ctx.author.id
        receiver_id = member.id
        now = time.time()
        pool = get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                for uid in sorted((sender_id, receiver_id)):
                    await self.database.ensure_wallet(conn, uid)

                await conn.fetch(
                    """
                    SELECT user_id FROM economy.wallets
                    WHERE user_id = ANY($1::BIGINT[])
                    ORDER BY user_id FOR UPDATE
                    """,
                    [sender_id, receiver_id],
                )

                balance = await conn.fetchval(
                    "SELECT balance FROM economy.wallets WHERE user_id = $1",
                    sender_id,
                )

                if balance < amount:
                    success = False
                else:
                    await conn.execute(
                        """
                        UPDATE economy.wallets
                        SET balance = balance - $1
                        WHERE user_id = $2
                        """,
                        amount,
                        sender_id,
                    )
                    await conn.execute(
                        """
                        UPDATE economy.wallets
                        SET balance = balance + $1
                        WHERE user_id = $2
                        """,
                        amount,
                        receiver_id,
                    )
                    await conn.execute(
                        """
                        INSERT INTO economy.transactions
                            (sender_id, receiver_id, amount, reason, created_at)
                        VALUES ($1, $2, $3, 'transfer', $4)
                        """,
                        sender_id,
                        receiver_id,
                        amount,
                        now,
                    )
                    success = True

        if not success:
            await ctx.send("❌ Недостаточно Риликов для перевода.")
            return

        await ctx.send(embed=self.make_embed(
            "💸 Перевод выполнен",
            f"**Отправитель:** {ctx.author.mention}\n"
            f"**Получатель:** {member.mention}\n"
            f"**Сумма:** {amount:,} Риликов",
            0x2ECC71,
        ))

    @commands.hybrid_command(
        name="give",
        description="Выдать Рилики участнику (владелец)",
    )
    async def give(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
    ):
        if ctx.author.id != OWNER_ID:
            await ctx.send("❌ Эта команда доступна только владельцу.")
            return

        if member.bot or amount <= 0 or amount > 1_000_000_000:
            await ctx.send("❌ Некорректный участник или сумма.")
            return

        now = time.time()
        pool = get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await self.database.ensure_wallet(conn, member.id)

                await conn.fetchrow(
                    """
                    SELECT user_id FROM economy.wallets
                    WHERE user_id = $1 FOR UPDATE
                    """,
                    member.id,
                )

                await conn.execute(
                    """
                    UPDATE economy.wallets
                    SET balance = balance + $1,
                        total_earned = total_earned + $1
                    WHERE user_id = $2
                    """,
                    amount,
                    member.id,
                )

                await conn.execute(
                    """
                    INSERT INTO economy.transactions
                        (sender_id, receiver_id, amount, reason, created_at)
                    VALUES ($1, $2, $3, 'owner_gift', $4)
                    """,
                    ctx.author.id,
                    member.id,
                    amount,
                    now,
                )

        await ctx.send(embed=self.make_embed(
            "👑 Выдача Риликов",
            f"{member.mention} получил **{amount:,} Риликов**.",
        ))

    @commands.hybrid_command(
        name="top",
        description="Посмотреть рейтинги участников",
    )
    async def top(
        self,
        ctx: commands.Context,
        category: str = "balance",
    ):
        categories = {
            "balance": ("💎 Богачи WeriGrief", "balance"),
            "work": ("🛠️ Лучшие работники", "work_level"),
        }

        if category not in categories:
            await ctx.send("Выбери рейтинг: `balance` или `work`.")
            return

        title, column = categories[category]
        pool = get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT user_id, balance, work_level
                FROM economy.wallets
                ORDER BY {column} DESC, user_id ASC
                LIMIT 10
                """
            )

        if not rows:
            await ctx.send("Пока рейтинг пуст.")
            return

        lines = []
        for position, row in enumerate(rows, start=1):
            user = self.bot.get_user(row["user_id"])
            name = user.display_name if user else f"Участник {row['user_id']}"

            value = (
                f"{row['balance']:,} Риликов"
                if category == "balance"
                else f"{row['work_level']} уровень"
            )
            lines.append(f"**{position}.** {name} — {value}")

        await ctx.send(embed=self.make_embed(title, "\n".join(lines)))

    @commands.hybrid_command(
        name="shop",
        description="Открыть интерактивный магазин WeriGrief",
    )
    async def shop(self, ctx: commands.Context):
        embed = self.make_embed(
            "🌙 Магазин WeriGrief",
            "Покупай привилегии за Рилики!\n\n"
            "👑 **VIP — 30 дней**\n"
            "**25 000 Риликов**\n\n"
            "🐾 Питомцы и 🎨 косметика появятся в следующих обновлениях.\n\n"
            "Нажми кнопку ниже, чтобы купить VIP или проверить баланс.",
        )
        embed.set_footer(text="WeriGrief • Магазин")
        await ctx.send(embed=embed, view=ShopView())

    @commands.hybrid_command(
        name="list",
        description="Показать список участников сервера",
    )
    @commands.guild_only()
    async def member_list(self, ctx: commands.Context):
        await ctx.defer()

        try:
            if not ctx.guild.chunked:
                await ctx.guild.chunk(cache=True)

            members = sorted(
                ctx.guild.members,
                key=lambda member: member.display_name.casefold(),
            )

            if not members:
                await ctx.send("Не удалось получить список участников.")
                return

            from src.modules.economy.list_view import MemberListView

            view = MemberListView(
                members=members,
                owner_id=ctx.author.id,
            )
            await ctx.send(embed=view.make_embed(), view=view)

        except discord.Forbidden:
            await ctx.send(
                "❌ Не удалось получить участников. "
                "Проверь включение Server Members Intent в Discord Developer Portal."
            )
        except Exception as error:
            await ctx.send(
                f"❌ Ошибка при загрузке списка: `{type(error).__name__}`"
            )

    @commands.hybrid_command(
        name="profile",
        description="Посмотреть профиль участника",
    )
    async def profile(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ):
        member = member or ctx.author
        wallet = await self.database.get_wallet(member.id)

        next_level_xp = wallet["work_level"] * 100
        current_xp = wallet["work_xp"]
        progress = min(100, int(current_xp / next_level_xp * 100))

        embed = self.make_embed(
            f"👤 Профиль {member.display_name}",
            f"💎 **Баланс:** {wallet['balance']:,} Риликов\n"
            f"📊 **Всего заработано:** {wallet['total_earned']:,} Риликов\n"
            f"🛠️ **Уровень работы:** {wallet['work_level']}\n"
            f"⭐ **Опыт:** {current_xp}/{next_level_xp} XP\n"
            f"📈 **Прогресс:** {progress}%\n"
            f"💼 **Количество смен:** {wallet['work_count']}",
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(EconomyCog(bot))
