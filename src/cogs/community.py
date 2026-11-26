"""Community features: economy, character guessing and clans."""

import logging
import random
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from src.core.config import GUILD_ID
from src.core.database import get_pool

log = logging.getLogger("weritime.community")

SHOP = {
    "badge_star": ("Значок «Звезда»", 150, "Коллекционный значок участника."),
    "badge_miner": ("Значок «Шахтёр»", 250, "Для тех, кто всегда находит алмазы."),
    "badge_builder": ("Значок «Строитель»", 300, "Коллекционный значок строителя."),
    "badge_ender": ("Значок «Эндер»", 450, "Редкий фиолетовый значок."),
    "title_veteran": ("Титул «Ветеран»", 600, "Коллекционный титул WeriTime."),
    "title_legend": ("Титул «Легенда»", 1200, "Коллекционный титул WeriTime."),
    "pet_slime": ("Питомец-слизень", 350, "Мягкий и немного липкий коллекционный предмет."),
    "pet_allay": ("Питомец-аллаэй", 700, "Коллекционный предмет в честь помощника."),
    "ticket_theme": ("Тема «Ночь»", 250, "Коллекционная тема профиля."),
    "ticket_theme2": ("Тема «Изумруд»", 400, "Коллекционная зелёная тема профиля."),
    "banner_sky": ("Баннер «Небо»", 500, "Коллекционный баннер профиля."),
    "banner_nether": ("Баннер «Незер»", 800, "Коллекционный баннер профиля."),
    "crate_common": ("Обычный сундук", 200, "Коллекционный сундук WeriTime."),
    "crate_rare": ("Редкий сундук", 900, "Редкий коллекционный сундук."),
    "badge_crown": ("Значок «Корона»", 2500, "Особый коллекционный значок."),
}

CHARACTERS = {
    "steve": ("Стив", "Классический герой в голубой футболке."),
    "alex": ("Алекс", "Героиня с рыжими волосами и зелёной рубашкой."),
    "creeper": ("Крипер", "Тихо подходит и громко взрывается."),
    "enderman": ("Эндермен", "Высокий житель Края, не любит зрительный контакт."),
    "villager": ("Житель", "Торгуется изумрудами и говорит «хмм»."),
    "warden": ("Варден", "Слепой страж глубокой тьмы."),
    "zombie": ("Зомби", "Ночью ищет игроков и двери."),
    "skeleton": ("Скелет", "Меткий лучник, который боится солнца."),
    "iron_golem": ("Железный голем", "Защищает жителей и бьёт очень сильно."),
    "allay": ("Аллаэй", "Летает и ищет предметы по нотам."),
    "witch": ("Ведьма", "Бросает зелья и живёт в болотной хижине."),
    "blaze": ("Ифрит", "Летает в крепости Незера и стреляет огненными шарами."),
}

JOKES = [
    "Почему крипер не играет в прятки? Его всегда находят по звуку «ш-ш-ш». 🙃",
    "Стив копал до упора. Упёрся в бедрок и решил, что это потолок карьеры.",
    "— Ты где? — В шахте. — А почему не отвечал? — Нашёл алмазы.",
    "Эндермен пришёл на собеседование. Увидел босса и телепортировался домой.",
    "Житель открыл магазин. Цены высокие, но зато каждый день новое «хмм». ",
    "Скелет хотел стать музыкантом, но у него всё время дребезжали кости.",
    "Варден не читает спойлеры. Он всё равно всё слышит заранее.",
    "— Почему ты опоздал? — Заблудился. — Но ты же админ! — Вот поэтому и заблудился в настройках.",
    "Крипер заказал торт. Курьер попросил не открывать дверь резко.",
    "Незер — отличное место для отпуска: жарко, красиво и домой быстро не уйдёшь.",
    "Аллаэй принёс мне блокнот. Я просил блок, но он старается.",
    "Когда сервер лагает, даже зомби идут задом наперёд.",
    "Кот убежал от крипера. Теперь это самая громкая история в деревне.",
    "— Сколько алмазов добыл? — Один. — Всего? — Нет, первый.",
    "Модератор пошёл отдыхать и всё равно попросил всех вести себя хорошо.",
]


class GuessView(discord.ui.View):
    def __init__(self, cog, user_id, answer, options):
        super().__init__(timeout=60)
        self.cog, self.user_id, self.answer = cog, user_id, answer
        select = discord.ui.Select(
            placeholder="Кто это?",
            options=[discord.SelectOption(label=CHARACTERS[key][0], value=key) for key in options],
        )
        select.callback = self.choose
        self.add_item(select)

    async def choose(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Эта игра создана другим участником.", ephemeral=True)
            return
        choice = interaction.data["values"][0]
        correct = choice == self.answer
        async with get_pool().acquire() as conn:
            await conn.execute(
                "INSERT INTO social.guess_scores(user_id,games,wins) VALUES($1,1,$2) "
                "ON CONFLICT(user_id) DO UPDATE SET games=social.guess_scores.games+1, wins=social.guess_scores.wins+$2",
                self.user_id, 1 if correct else 0,
            )
            if correct:
                await conn.execute("INSERT INTO economy.wallets(user_id) VALUES($1) ON CONFLICT DO NOTHING", self.user_id)
                await conn.execute("UPDATE economy.wallets SET balance=balance+75,total_earned=total_earned+75 WHERE user_id=$1", self.user_id)
                await conn.execute(
                    "INSERT INTO economy.transactions(sender_id,receiver_id,amount,reason,created_at) VALUES(NULL,$1,75,'Угадал персонажа',EXTRACT(EPOCH FROM NOW()))",
                    self.user_id,
                )
        text = f"✅ Верно! Это **{CHARACTERS[self.answer][0]}**. +75 WeriCoins." if correct else f"❌ Это был **{CHARACTERS[self.answer][0]}**. Попробуй ещё раз!"
        await interaction.response.edit_message(content=text, embed=None, view=None)


class ClanInviteView(discord.ui.View):
    def __init__(self, cog, clan_id: int, target_id: int):
        super().__init__(timeout=None)
        self.cog, self.clan_id, self.target_id = cog, clan_id, target_id
        for label, accept in (("Принять", True), ("Отклонить", False)):
            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.success if accept else discord.ButtonStyle.secondary,
                custom_id=f"weritime:clan:{'yes' if accept else 'no'}:{clan_id}:{target_id}",
            )
            button.callback = self.accept if accept else self.decline
            self.add_item(button)

    async def accept(self, interaction: discord.Interaction):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("Это приглашение адресовано другому участнику.", ephemeral=True)
            return
        await self.cog.accept_clan_invite(interaction, self.clan_id, self.target_id)

    async def decline(self, interaction: discord.Interaction):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("Это приглашение адресовано другому участнику.", ephemeral=True)
            return
        async with get_pool().acquire() as conn:
            await conn.execute("DELETE FROM social.clan_invites WHERE clan_id=$1 AND user_id=$2", self.clan_id, self.target_id)
        await interaction.response.edit_message(content="Приглашение отклонено.", embed=None, view=None)


class CommunityCog(commands.Cog, name="Community"):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        async with get_pool().acquire() as conn:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS economy")
            await conn.execute("CREATE SCHEMA IF NOT EXISTS social")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS economy.wallets (
                    user_id BIGINT PRIMARY KEY, balance BIGINT NOT NULL DEFAULT 0,
                    work_xp BIGINT NOT NULL DEFAULT 0, work_level INTEGER NOT NULL DEFAULT 1,
                    work_count BIGINT NOT NULL DEFAULT 0, total_earned BIGINT NOT NULL DEFAULT 0
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS economy.cooldowns (
                    user_id BIGINT NOT NULL, action TEXT NOT NULL, expires_at DOUBLE PRECISION NOT NULL,
                    PRIMARY KEY(user_id,action)
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS economy.transactions (
                    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    sender_id BIGINT, receiver_id BIGINT, amount BIGINT NOT NULL,
                    reason TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS economy.shop_inventory (
                    user_id BIGINT NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(user_id,item_id)
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS social.clans (
                    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    name TEXT NOT NULL, tag TEXT UNIQUE NOT NULL, owner_id BIGINT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS social.clan_members (
                    user_id BIGINT PRIMARY KEY, clan_id BIGINT NOT NULL REFERENCES social.clans(id) ON DELETE CASCADE,
                    joined_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS social.clan_invites (
                    clan_id BIGINT NOT NULL REFERENCES social.clans(id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL, inviter_id BIGINT NOT NULL, expires_at DOUBLE PRECISION NOT NULL,
                    PRIMARY KEY(clan_id,user_id)
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS social.guess_scores (
                    user_id BIGINT PRIMARY KEY, games INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0
                )
            """)
            invites = await conn.fetch("SELECT clan_id,user_id FROM social.clan_invites WHERE expires_at>$1", time.time())
        for invite in invites:
            self.bot.add_view(ClanInviteView(self, invite["clan_id"], invite["user_id"]))

    async def wallet_balance(self, user_id: int) -> int:
        async with get_pool().acquire() as conn:
            await conn.execute("INSERT INTO economy.wallets(user_id) VALUES($1) ON CONFLICT DO NOTHING", user_id)
            return await conn.fetchval("SELECT balance FROM economy.wallets WHERE user_id=$1", user_id)

    @app_commands.command(name="balance", description="Посмотреть баланс WeriCoins")
    async def balance(self, interaction: discord.Interaction):
        amount = await self.wallet_balance(interaction.user.id)
        await interaction.response.send_message(f"💰 Баланс: **{amount:,} WeriCoins**", ephemeral=True)

    async def reward(self, interaction, action: str, cooldown: int, low: int, high: int, label: str):
        now = time.time()
        amount = random.randint(low, high)
        async with get_pool().acquire() as conn:
            async with conn.transaction():
                await conn.execute("INSERT INTO economy.wallets(user_id) VALUES($1) ON CONFLICT DO NOTHING", interaction.user.id)
                row = await conn.fetchrow(
                    "INSERT INTO economy.cooldowns(user_id,action,expires_at) VALUES($1,$2,$3) "
                    "ON CONFLICT(user_id,action) DO UPDATE SET expires_at=EXCLUDED.expires_at "
                    "WHERE economy.cooldowns.expires_at<=$4 RETURNING expires_at",
                    interaction.user.id, action, now + cooldown, now,
                )
                if row is None:
                    expiry = await conn.fetchval("SELECT expires_at FROM economy.cooldowns WHERE user_id=$1 AND action=$2", interaction.user.id, action)
                    remaining = max(1, int(expiry - now))
                    await interaction.response.send_message(f"⏳ Попробуй снова через **{remaining // 3600}ч {(remaining % 3600) // 60}м**.", ephemeral=True)
                    return
                await conn.execute("UPDATE economy.wallets SET balance=balance+$2,total_earned=total_earned+$2,work_count=work_count+1,work_xp=work_xp+10 WHERE user_id=$1", interaction.user.id, amount)
                await conn.execute("INSERT INTO economy.transactions(sender_id,receiver_id,amount,reason,created_at) VALUES(NULL,$1,$2,$3,$4)", interaction.user.id, amount, label, now)
        await interaction.response.send_message(f"{label}: **+{amount} WeriCoins**. Баланс: **{await self.wallet_balance(interaction.user.id)}**.", ephemeral=True)

    @app_commands.command(name="daily", description="Забрать ежедневную награду")
    async def daily(self, interaction: discord.Interaction):
        await self.reward(interaction, "daily", 86400, 100, 250, "🎁 Ежедневная награда")

    @app_commands.command(name="work", description="Выполнить работу и заработать монеты")
    async def work(self, interaction: discord.Interaction):
        await self.reward(interaction, "work", 3600, 40, 120, "🧰 Смена завершена")

    @app_commands.command(name="shop", description="Посмотреть магазин WeriTime")
    async def shop(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🛍️ Магазин WeriTime", color=0x2ECC71, description="15 коллекционных предметов. Покупки сохраняются в инвентаре.")
        for key, (name, price, desc) in SHOP.items():
            embed.add_field(name=f"{name} · {price} WeriCoins", value=f"`{key}` — {desc}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="buy", description="Купить предмет в магазине")
    @app_commands.choices(item=[app_commands.Choice(name=f"{name} — {price}", value=key) for key, (name, price, _desc) in SHOP.items()])
    async def buy(self, interaction: discord.Interaction, item: app_commands.Choice[str]):
        name, price, _desc = SHOP[item.value]
        async with get_pool().acquire() as conn:
            async with conn.transaction():
                await conn.execute("INSERT INTO economy.wallets(user_id) VALUES($1) ON CONFLICT DO NOTHING", interaction.user.id)
                row = await conn.fetchrow("SELECT balance FROM economy.wallets WHERE user_id=$1 FOR UPDATE", interaction.user.id)
                if row["balance"] < price:
                    await interaction.response.send_message(f"Не хватает {price - row['balance']} WeriCoins.", ephemeral=True)
                    return
                await conn.execute("UPDATE economy.wallets SET balance=balance-$2 WHERE user_id=$1", interaction.user.id, price)
                await conn.execute("INSERT INTO economy.shop_inventory(user_id,item_id,quantity) VALUES($1,$2,1) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=economy.shop_inventory.quantity+1", interaction.user.id, item.value)
                await conn.execute("INSERT INTO economy.transactions(sender_id,receiver_id,amount,reason,created_at) VALUES($1,NULL,$2,$3,$4)", interaction.user.id, price, f"Магазин: {name}", time.time())
        await interaction.response.send_message(f"✅ Куплено: **{name}**. Проверь `/inventory`.", ephemeral=True)

    @app_commands.command(name="inventory", description="Посмотреть свой инвентарь")
    async def inventory(self, interaction: discord.Interaction):
        async with get_pool().acquire() as conn:
            rows = await conn.fetch("SELECT item_id,quantity FROM economy.shop_inventory WHERE user_id=$1 ORDER BY item_id", interaction.user.id)
        if not rows:
            await interaction.response.send_message("🎒 Инвентарь пуст. Посмотри `/shop`.", ephemeral=True)
            return
        lines = [f"• **{SHOP[row['item_id']][0]}** × {row['quantity']}" for row in rows if row["item_id"] in SHOP]
        await interaction.response.send_message("🎒 **Инвентарь**\n" + "\n".join(lines), ephemeral=True)

    @app_commands.command(name="transfer", description="Перевести WeriCoins участнику")
    async def transfer(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1000000]):
        if member.bot or member.id == interaction.user.id:
            await interaction.response.send_message("Выбери другого участника, не бота.", ephemeral=True)
            return
        async with get_pool().acquire() as conn:
            async with conn.transaction():
                await conn.execute("INSERT INTO economy.wallets(user_id) VALUES($1),($2) ON CONFLICT DO NOTHING", interaction.user.id, member.id)
                sender = await conn.fetchrow("SELECT balance FROM economy.wallets WHERE user_id=$1 FOR UPDATE", interaction.user.id)
                if sender["balance"] < amount:
                    await interaction.response.send_message("Недостаточно монет.", ephemeral=True)
                    return
                await conn.execute("UPDATE economy.wallets SET balance=balance-$2 WHERE user_id=$1", interaction.user.id, amount)
                await conn.execute("UPDATE economy.wallets SET balance=balance+$2,total_earned=total_earned+$2 WHERE user_id=$1", member.id, amount)
                await conn.execute("INSERT INTO economy.transactions(sender_id,receiver_id,amount,reason,created_at) VALUES($1,$2,$3,'Перевод', $4)", interaction.user.id, member.id, amount, time.time())
        await interaction.response.send_message(f"✅ Переведено **{amount} WeriCoins** участнику {member.mention}.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="guess_character", description="Угадай персонажа Minecraft по подсказке")
    async def guess_character(self, interaction: discord.Interaction):
        answer = random.choice(list(CHARACTERS))
        options = random.sample([key for key in CHARACTERS if key != answer], 3) + [answer]
        random.shuffle(options)
        embed = discord.Embed(title="🎮 Угадай персонажа", description=CHARACTERS[answer][1], color=0x9B59B6)
        await interaction.response.send_message(embed=embed, view=GuessView(self, interaction.user.id, answer, options), ephemeral=True)

    @app_commands.command(name="guess_leaderboard", description="Таблица побед в игре «Угадай персонажа»")
    async def guess_leaderboard(self, interaction: discord.Interaction):
        async with get_pool().acquire() as conn:
            rows = await conn.fetch("SELECT user_id,wins,games FROM social.guess_scores ORDER BY wins DESC,games ASC LIMIT 10")
        if not rows:
            await interaction.response.send_message("Пока никто не играл.", ephemeral=True)
            return
        lines = [f"**{i}.** <@{row['user_id']}> — {row['wins']} побед из {row['games']}" for i, row in enumerate(rows, 1)]
        await interaction.response.send_message(embed=discord.Embed(title="🏆 Угадай персонажа", description="\n".join(lines), color=0xF1C40F), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="joke", description="Случайная шутка WeriTime")
    async def joke(self, interaction: discord.Interaction):
        await interaction.response.send_message(random.choice(JOKES))

    async def member_clan(self, user_id):
        async with get_pool().acquire() as conn:
            return await conn.fetchrow("SELECT c.id,c.name,c.tag,c.owner_id FROM social.clan_members m JOIN social.clans c ON c.id=m.clan_id WHERE m.user_id=$1", user_id)

    @staticmethod
    async def apply_clan_nick(member: discord.Member, tag: str | None):
        current = member.nick or member.name
        current = re.sub(r"^\[[A-Z0-9]{2,5}\]\s*", "", current)
        nick = f"[{tag}] {current}" if tag else current
        await member.edit(nick=nick[:32], reason="Обновление тега клана WeriTime")

    @app_commands.command(name="clan_create", description="Создать клан с коротким тегом")
    async def clan_create(self, interaction: discord.Interaction, name: app_commands.Range[str, 3, 32], tag: app_commands.Range[str, 2, 5]):
        normalized_tag = tag.upper()
        if not re.fullmatch(r"[A-Z0-9]{2,5}", normalized_tag):
            await interaction.response.send_message("Тег должен состоять из 2–5 латинских букв или цифр.", ephemeral=True)
            return
        if await self.member_clan(interaction.user.id):
            await interaction.response.send_message("Ты уже состоишь в клане.", ephemeral=True)
            return
        async with get_pool().acquire() as conn:
            async with conn.transaction():
                clan_id = await conn.fetchval("INSERT INTO social.clans(name,tag,owner_id) VALUES($1,$2,$3) RETURNING id", name, normalized_tag, interaction.user.id)
                await conn.execute("INSERT INTO social.clan_members(user_id,clan_id) VALUES($1,$2)", interaction.user.id, clan_id)
        try:
            if isinstance(interaction.user, discord.Member):
                await self.apply_clan_nick(interaction.user, normalized_tag)
        except discord.Forbidden:
            pass
        await interaction.response.send_message(f"⚔️ Клан **{name}** [`{normalized_tag}`] создан!", ephemeral=True)

    @app_commands.command(name="clan_invite", description="Пригласить участника в свой клан")
    async def clan_invite(self, interaction: discord.Interaction, member: discord.Member):
        clan = await self.member_clan(interaction.user.id)
        if not clan or clan["owner_id"] != interaction.user.id:
            await interaction.response.send_message("Приглашать может только владелец клана.", ephemeral=True)
            return
        if member.bot or await self.member_clan(member.id):
            await interaction.response.send_message("Участник уже в клане или это бот.", ephemeral=True)
            return
        async with get_pool().acquire() as conn:
            await conn.execute("INSERT INTO social.clan_invites(clan_id,user_id,inviter_id,expires_at) VALUES($1,$2,$3,$4) ON CONFLICT(clan_id,user_id) DO UPDATE SET inviter_id=EXCLUDED.inviter_id,expires_at=EXCLUDED.expires_at", clan["id"], member.id, interaction.user.id, time.time()+172800)
        view = ClanInviteView(self, clan["id"], member.id)
        self.bot.add_view(view)
        try:
            await member.send(f"⚔️ **{clan['name']}** [`{clan['tag']}`] приглашает тебя в клан WeriTime.", view=view)
        except discord.Forbidden:
            await interaction.response.send_message("У участника закрыты ЛС. Приглашение сохранено на 48 часов.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Приглашение отправлено {member.mention} в личные сообщения.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def accept_clan_invite(self, interaction, clan_id, user_id):
        async with get_pool().acquire() as conn:
            async with conn.transaction():
                invite = await conn.fetchrow("SELECT expires_at FROM social.clan_invites WHERE clan_id=$1 AND user_id=$2 FOR UPDATE", clan_id, user_id)
                if not invite or invite["expires_at"] < time.time():
                    await interaction.response.edit_message(content="Приглашение истекло.", embed=None, view=None)
                    return
                if await conn.fetchval("SELECT 1 FROM social.clan_members WHERE user_id=$1", user_id):
                    await interaction.response.edit_message(content="Ты уже в клане.", embed=None, view=None)
                    return
                clan = await conn.fetchrow("SELECT name,tag FROM social.clans WHERE id=$1", clan_id)
                await conn.execute("INSERT INTO social.clan_members(user_id,clan_id) VALUES($1,$2)", user_id, clan_id)
                await conn.execute("DELETE FROM social.clan_invites WHERE user_id=$1", user_id)
        try:
            guild = self.bot.get_guild(GUILD_ID)
            member = guild.get_member(user_id) if guild else None
            if member:
                await self.apply_clan_nick(member, clan["tag"])
        except discord.Forbidden:
            pass
        await interaction.response.edit_message(content=f"✅ Ты вступил в клан **{clan['name']}** [`{clan['tag']}`].", embed=None, view=None)

    @app_commands.command(name="clan_leave", description="Выйти из своего клана")
    async def clan_leave(self, interaction: discord.Interaction):
        clan = await self.member_clan(interaction.user.id)
        if not clan:
            await interaction.response.send_message("Ты не состоишь в клане.", ephemeral=True)
            return
        if clan["owner_id"] == interaction.user.id:
            await interaction.response.send_message("Передай лидерство или распусти клан через `/clan_disband`.", ephemeral=True)
            return
        async with get_pool().acquire() as conn:
            await conn.execute("DELETE FROM social.clan_members WHERE user_id=$1", interaction.user.id)
        try:
            if isinstance(interaction.user, discord.Member):
                await self.apply_clan_nick(interaction.user, None)
        except discord.Forbidden:
            pass
        await interaction.response.send_message(f"Ты вышел из клана **{clan['name']}**.", ephemeral=True)

    @app_commands.command(name="clan_disband", description="Распустить свой клан")
    async def clan_disband(self, interaction: discord.Interaction):
        clan = await self.member_clan(interaction.user.id)
        if not clan or clan["owner_id"] != interaction.user.id:
            await interaction.response.send_message("Распустить клан может только его владелец.", ephemeral=True)
            return
        async with get_pool().acquire() as conn:
            members = await conn.fetch("SELECT user_id FROM social.clan_members WHERE clan_id=$1", clan["id"])
            await conn.execute("DELETE FROM social.clans WHERE id=$1", clan["id"])
        for row in members:
            member = interaction.guild.get_member(row["user_id"]) if interaction.guild else None
            if member:
                try:
                    await self.apply_clan_nick(member, None)
                except discord.Forbidden:
                    pass
        await interaction.response.send_message(f"Клан **{clan['name']}** распущен.", ephemeral=True)

    @app_commands.command(name="clan_info", description="Показать состав и информацию о клане")
    async def clan_info(self, interaction: discord.Interaction, tag: str = ""):
        async with get_pool().acquire() as conn:
            if tag:
                clan = await conn.fetchrow("SELECT id,name,tag,owner_id FROM social.clans WHERE lower(tag)=lower($1)", tag.strip("[]"))
            else:
                clan = await self.member_clan(interaction.user.id)
            if not clan:
                await interaction.response.send_message("Клан не найден. Укажи тег или создай клан.", ephemeral=True)
                return
            members = await conn.fetch("SELECT user_id FROM social.clan_members WHERE clan_id=$1 ORDER BY joined_at", clan["id"])
        mentions = ", ".join(f"<@{row['user_id']}>" for row in members[:30]) or "Пока пусто"
        embed = discord.Embed(title=f"⚔️ {clan['name']} [{clan['tag']}]", color=0x8E44AD, description=f"**Лидер:** <@{clan['owner_id']}>\n**Участников:** {len(members)}\n\n{mentions}")
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.guild.id != GUILD_ID:
            return
        clan = await self.member_clan(member.id)
        if clan:
            try:
                await self.apply_clan_nick(member, clan["tag"])
            except discord.Forbidden:
                log.info("Could not restore clan tag for %s", member.id)


async def setup(bot):
    await bot.add_cog(CommunityCog(bot))
