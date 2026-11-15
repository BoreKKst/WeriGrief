import time

from src.core.database import get_pool


PETS = {
    "cat": {
        "name": "Лунный кот",
        "emoji": "🐱",
        "price": 2500,
        "description": "Спокойный питомец с лунным характером.",
    },
    "fox": {
        "name": "Аметистовый лис",
        "emoji": "🦊",
        "price": 5000,
        "description": "Хитрый и энергичный спутник.",
    },
    "dragon": {
        "name": "Кристальный дракон",
        "emoji": "🐉",
        "price": 15000,
        "description": "Редкий дракон с кристальной чешуёй.",
    },
}

FOOD = {
    "snack": {
        "name": "Вкусняшка",
        "emoji": "🍖",
        "price": 250,
        "hunger": 35,
    },
    "premium": {
        "name": "Лунный корм",
        "emoji": "🌙",
        "price": 600,
        "hunger": 60,
    },
}


class PetsDatabase:
    @staticmethod
    async def ensure_wallet(conn, user_id):
        await conn.execute(
            """
            INSERT INTO economy.wallets (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )

    async def balance(self, user_id):
        pool = get_pool()
        async with pool.acquire() as conn:
            await self.ensure_wallet(conn, user_id)
            return await conn.fetchval(
                "SELECT balance FROM economy.wallets WHERE user_id = $1",
                user_id,
            )

    async def get_pets(self, user_id):
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM pets.pets
                WHERE owner_id = $1 OR co_owner_id = $1
                ORDER BY id DESC
                """,
                user_id,
            )
            return [dict(row) for row in rows]

    async def get_pet(self, pet_id):
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM pets.pets WHERE id = $1",
                pet_id,
            )
            return dict(row) if row else None

    @staticmethod
    def can_access(pet, user_id):
        return bool(
            pet and user_id in (pet["owner_id"], pet["co_owner_id"])
        )

    async def purchase_pet(self, user_id, species, name):
        info = PETS.get(species)
        if not info:
            return False, "Неизвестный питомец."

        pool = get_pool()
        now = time.time()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await self.ensure_wallet(conn, user_id)

                await conn.fetchrow(
                    """
                    SELECT user_id FROM economy.wallets
                    WHERE user_id = $1 FOR UPDATE
                    """,
                    user_id,
                )

                count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM pets.pets
                    WHERE owner_id = $1
                    """,
                    user_id,
                )

                if count >= 5:
                    return False, "У тебя уже максимум 5 питомцев."

                changed = await conn.fetchval(
                    """
                    UPDATE economy.wallets
                    SET balance = balance - $1
                    WHERE user_id = $2 AND balance >= $1
                    RETURNING user_id
                    """,
                    info["price"],
                    user_id,
                )

                if changed is None:
                    return False, "Недостаточно Риликов."

                await conn.execute(
                    """
                    INSERT INTO pets.pets
                        (owner_id, species, name, created_at, last_care)
                    VALUES ($1, $2, $3, $4, $4)
                    """,
                    user_id,
                    species,
                    name[:32],
                    now,
                )

                await conn.execute(
                    """
                    INSERT INTO economy.transactions
                        (sender_id, receiver_id, amount, reason, created_at)
                    VALUES ($1, NULL, $2, $3, $4)
                    """,
                    user_id,
                    info["price"],
                    "Покупка питомца: " + info["name"],
                    now,
                )

        return True, f"Ты приобрёл питомца: {info['name']}!"

    async def purchase_food(self, user_id, item):
        info = FOOD.get(item)
        if not info:
            return False, "Неизвестный корм."

        pool = get_pool()
        now = time.time()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await self.ensure_wallet(conn, user_id)

                await conn.fetchrow(
                    """
                    SELECT user_id FROM economy.wallets
                    WHERE user_id = $1 FOR UPDATE
                    """,
                    user_id,
                )

                changed = await conn.fetchval(
                    """
                    UPDATE economy.wallets
                    SET balance = balance - $1
                    WHERE user_id = $2 AND balance >= $1
                    RETURNING user_id
                    """,
                    info["price"],
                    user_id,
                )

                if changed is None:
                    return False, "Недостаточно Риликов."

                await conn.execute(
                    """
                    INSERT INTO pets.pet_inventory (user_id, item, quantity)
                    VALUES ($1, $2, 1)
                    ON CONFLICT (user_id, item)
                    DO UPDATE SET quantity = pets.pet_inventory.quantity + 1
                    """,
                    user_id,
                    item,
                )

                await conn.execute(
                    """
                    INSERT INTO economy.transactions
                        (sender_id, receiver_id, amount, reason, created_at)
                    VALUES ($1, NULL, $2, $3, $4)
                    """,
                    user_id,
                    info["price"],
                    "Покупка корма: " + info["name"],
                    now,
                )

        return True, f"Куплено: {info['name']}!"

    async def inventory(self, user_id):
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT item, quantity
                FROM pets.pet_inventory
                WHERE user_id = $1
                """,
                user_id,
            )
            return {row["item"]: row["quantity"] for row in rows}

    async def refresh(self, pet_id):
        pool = get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT * FROM pets.pets
                    WHERE id = $1 FOR UPDATE
                    """,
                    pet_id,
                )

                if not row:
                    return None

                pet = dict(row)
                now = time.time()
                elapsed = max(0, now - pet["last_care"])
                hours = elapsed / 3600
                hunger_loss = int(hours * 4)
                mood_loss = int(hours * 3)

                if hunger_loss or mood_loss:
                    await conn.execute(
                        """
                        UPDATE pets.pets
                        SET hunger = $1, mood = $2, last_care = $3
                        WHERE id = $4
                        """,
                        max(0, pet["hunger"] - hunger_loss),
                        max(0, pet["mood"] - mood_loss),
                        now,
                        pet_id,
                    )

                    pet = dict(await conn.fetchrow(
                        "SELECT * FROM pets.pets WHERE id = $1",
                        pet_id,
                    ))

                return pet

    @staticmethod
    async def add_xp(conn, pet_id, gained):
        row = await conn.fetchrow(
            """
            SELECT level, xp FROM pets.pets
            WHERE id = $1 FOR UPDATE
            """,
            pet_id,
        )

        if not row:
            return None

        level = row["level"]
        xp = row["xp"] + gained

        while xp >= level * 100:
            xp -= level * 100
            level += 1

        await conn.execute(
            """
            UPDATE pets.pets SET level = $1, xp = $2
            WHERE id = $3
            """,
            level,
            xp,
            pet_id,
        )
        return level

    async def feed(self, pet_id, user_id):
        pool = get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                pet = await conn.fetchrow(
                    """
                    SELECT * FROM pets.pets
                    WHERE id = $1 FOR UPDATE
                    """,
                    pet_id,
                )

                if not pet or not self.can_access(dict(pet), user_id):
                    return False, "У тебя нет доступа к этому питомцу."

                inventory = await conn.fetchrow(
                    """
                    SELECT item, quantity FROM pets.pet_inventory
                    WHERE user_id = $1 AND quantity > 0
                    ORDER BY CASE item WHEN 'premium' THEN 0 ELSE 1 END
                    LIMIT 1 FOR UPDATE
                    """,
                    user_id,
                )

                if not inventory:
                    return False, "У тебя нет еды. Купи её в `/petshop`."

                item = inventory["item"]
                food = FOOD.get(item)

                if not food:
                    return False, "Неизвестный корм в инвентаре."

                await conn.execute(
                    """
                    UPDATE pets.pet_inventory
                    SET quantity = quantity - 1
                    WHERE user_id = $1 AND item = $2
                    """,
                    user_id,
                    item,
                )

                await conn.execute(
                    """
                    UPDATE pets.pets
                    SET hunger = $1, last_care = $2
                    WHERE id = $3
                    """,
                    min(100, pet["hunger"] + food["hunger"]),
                    time.time(),
                    pet_id,
                )

                level = await self.add_xp(conn, pet_id, 10)

        return True, f"{food['emoji']} Питомец покушал! +10 XP. Уровень: {level}"

    async def play(self, pet_id, user_id):
        pool = get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                pet = await conn.fetchrow(
                    """
                    SELECT * FROM pets.pets
                    WHERE id = $1 FOR UPDATE
                    """,
                    pet_id,
                )

                if not pet or not self.can_access(dict(pet), user_id):
                    return False, "У тебя нет доступа к этому питомцу."

                if pet["hunger"] < 10:
                    return False, "Питомец слишком голоден. Сначала покорми его."

                await conn.execute(
                    """
                    UPDATE pets.pets
                    SET mood = $1, hunger = $2, last_care = $3
                    WHERE id = $4
                    """,
                    min(100, pet["mood"] + 20),
                    max(0, pet["hunger"] - 10),
                    time.time(),
                    pet_id,
                )

                level = await self.add_xp(conn, pet_id, 15)

        return True, f"🎾 Вы отлично поиграли! +15 XP. Уровень: {level}"

    async def rename(self, pet_id, user_id, name):
        pool = get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE pets.pets
                SET name = $1
                WHERE id = $2 AND (owner_id = $3 OR co_owner_id = $3)
                """,
                name[:32],
                pet_id,
                user_id,
            )
            return result == "UPDATE 1"

    async def set_coowner(self, pet_id, owner_id, co_owner_id):
        if co_owner_id == owner_id:
            return False

        pool = get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE pets.pets
                SET co_owner_id = $1
                WHERE id = $2 AND owner_id = $3
                """,
                co_owner_id,
                pet_id,
                owner_id,
            )
            return result == "UPDATE 1"
