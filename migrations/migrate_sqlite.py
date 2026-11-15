import asyncio
import sqlite3
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parent.parent
BACKUP = ROOT / "backups" / "postgres_migration"

ECONOMY = BACKUP / "werigrief_economy_20261115_135718.sqlite3"
PLAYTIME = BACKUP / "werigrief_playtime_20261115_135718.sqlite3"


def read_rows(path, query):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(query).fetchall()]


async def migrate():
    pool = await asyncpg.create_pool(
        host="127.0.0.1",
        port=5432,
        database="werigrief",
        user="werigrief",
        min_size=1,
        max_size=2,
    )

    try:
        economy_data = {
            "wallets": read_rows(ECONOMY, "SELECT * FROM wallets"),
            "cooldowns": read_rows(ECONOMY, "SELECT * FROM cooldowns"),
            "transactions": read_rows(ECONOMY, "SELECT * FROM transactions"),
        }

        playtime_data = {
            "totals": read_rows(PLAYTIME, "SELECT * FROM totals"),
            "daily": read_rows(PLAYTIME, "SELECT * FROM daily"),
            "day_status": read_rows(PLAYTIME, "SELECT * FROM day_status"),
            "credited_days": read_rows(PLAYTIME, "SELECT * FROM credited_days"),
        }

        expected = {
            "economy.wallets": len(economy_data["wallets"]),
            "economy.cooldowns": len(economy_data["cooldowns"]),
            "economy.transactions": len(economy_data["transactions"]),
            "playtime.totals": len(playtime_data["totals"]),
            "playtime.daily": len(playtime_data["daily"]),
            "playtime.day_status": len(playtime_data["day_status"]),
            "playtime.credited_days": len(playtime_data["credited_days"]),
        }

        async with pool.acquire() as conn:
            async with conn.transaction():
                for table in expected:
                    count = await conn.fetchval(
                        f"SELECT COUNT(*) FROM {table}"
                    )
                    if count != 0:
                        raise RuntimeError(
                            f"{table} уже содержит {count} строк. "
                            "Перенос отменён."
                        )

                for row in economy_data["wallets"]:
                    await conn.execute("""
                        INSERT INTO economy.wallets
                        (user_id, balance, work_xp, work_level,
                         work_count, total_earned)
                        VALUES ($1, $2, $3, $4, $5, $6)
                    """, row["user_id"], row["balance"],
                         row["work_xp"], row["work_level"],
                         row["work_count"], row["total_earned"])

                for row in economy_data["cooldowns"]:
                    await conn.execute("""
                        INSERT INTO economy.cooldowns
                        (user_id, action, expires_at)
                        VALUES ($1, $2, $3)
                    """, row["user_id"], row["action"],
                         row["expires_at"])

                for row in economy_data["transactions"]:
                    await conn.execute("""
                        INSERT INTO economy.transactions
                        (id, sender_id, receiver_id, amount, reason, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6)
                    """, row["id"], row["sender_id"],
                         row["receiver_id"], row["amount"],
                         row["reason"], row["created_at"])

                for row in playtime_data["totals"]:
                    await conn.execute("""
                        INSERT INTO playtime.totals (nick, seconds)
                        VALUES ($1, $2)
                    """, row["nick"], row["seconds"])

                for row in playtime_data["daily"]:
                    await conn.execute("""
                        INSERT INTO playtime.daily (nick, day, seconds)
                        VALUES ($1, $2, $3)
                    """, row["nick"], row["day"], row["seconds"])

                for row in playtime_data["day_status"]:
                    await conn.execute("""
                        INSERT INTO playtime.day_status (day, reliable)
                        VALUES ($1, $2)
                    """, row["day"], bool(row["reliable"]))

                for row in playtime_data["credited_days"]:
                    await conn.execute("""
                        INSERT INTO playtime.credited_days (day, credited_at)
                        VALUES ($1, $2)
                    """, row["day"], row["credited_at"])

                for table, count in expected.items():
                    actual = await conn.fetchval(
                        f"SELECT COUNT(*) FROM {table}"
                    )
                    if actual != count:
                        raise RuntimeError(
                            f"{table}: ожидалось {count}, получено {actual}"
                        )

                await conn.execute("""
                    SELECT setval(
                        pg_get_serial_sequence(
                            'economy.transactions', 'id'
                        ),
                        COALESCE((SELECT MAX(id)
                                  FROM economy.transactions), 1),
                        EXISTS(SELECT 1 FROM economy.transactions)
                    )
                """)

        print("МИГРАЦИЯ УСПЕШНА!")
        for table, count in expected.items():
            print(f"OK {table}: {count} строк")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(migrate())
