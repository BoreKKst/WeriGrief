"""Minecraft Query playtime tracking — PostgreSQL."""

import asyncio
import socket
import struct
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.core.config import (
    log,
    MC_QUERY_HOST,
    MC_QUERY_PORT,
    MC_QUERY_INTERVAL,
    MC_QUERY_TIMEOUT,
)
from src.core.database import get_pool

MSK = ZoneInfo("Europe/Moscow")


class QueryPlaytimeStore:
    def __init__(self):
        self.task = None
        self.previous_online = set()
        self.last_poll = None
        self.max_gap = max(90, MC_QUERY_INTERVAL * 3)

    @staticmethod
    def _key(nick):
        return str(nick).strip().casefold()

    async def ensure_staff(self, nick, legacy_hours=0):
        key = self._key(nick)
        try:
            hours = max(0.0, float(str(legacy_hours).replace(",", ".")))
        except (ValueError, TypeError):
            hours = 0.0

        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO playtime.totals(nick, seconds)
                VALUES($1, $2)
                ON CONFLICT(nick) DO NOTHING
                """,
                key,
                round(hours * 3600),
            )

    def _query_players(self):
        address = (MC_QUERY_HOST, MC_QUERY_PORT)
        session = b"\x01\x02\x03\x04"

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(MC_QUERY_TIMEOUT)

            sock.sendto(b"\xfe\xfd\x09" + session, address)
            challenge, _ = sock.recvfrom(4096)

            if len(challenge) < 6 or challenge[0] != 9:
                raise RuntimeError("Некорректный Query challenge")

            token_text = challenge[5:].split(b"\x00", 1)[0]
            token = int(token_text.decode("ascii"))

            request = (
                b"\xfe\xfd\x00"
                + session
                + struct.pack(">i", token)
                + b"\x00\x00\x00\x00"
            )
            sock.sendto(request, address)
            response, _ = sock.recvfrom(65535)

        if len(response) < 16 or response[0] != 0:
            raise RuntimeError("Некорректный Query full-stat ответ")

        payload = response[16:]
        marker = b"player_\x00"
        index = payload.find(marker)

        if index < 0:
            raise RuntimeError("В Query-ответе отсутствует список игроков")

        player_blob = payload[index + len(marker):]
        names = [
            item.decode("utf-8", errors="replace").strip()
            for item in player_blob.split(b"\x00")
            if item.strip()
        ]
        return {self._key(name) for name in names if name}

    async def _mark_unreliable(self, start, end):
        day = start.astimezone(MSK).date()
        last_day = end.astimezone(MSK).date()

        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                while day <= last_day:
                    await conn.execute(
                        """
                        INSERT INTO playtime.day_status(day, reliable)
                        VALUES($1, FALSE)
                        ON CONFLICT(day)
                        DO UPDATE SET reliable = FALSE
                        """,
                        day.isoformat(),
                    )
                    day += timedelta(days=1)

    async def _add_interval(self, nick, start, end):
        cursor = start
        total_added = 0

        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                while cursor < end:
                    local = cursor.astimezone(MSK)
                    next_midnight = (
                        local.replace(
                            hour=0, minute=0, second=0, microsecond=0
                        ) + timedelta(days=1)
                    )
                    boundary = next_midnight.astimezone(timezone.utc)
                    segment_end = min(end, boundary)
                    seconds = int((segment_end - cursor).total_seconds())

                    if seconds > 0:
                        day_key = local.date().isoformat()

                        await conn.execute(
                            """
                            INSERT INTO playtime.day_status(day, reliable)
                            VALUES($1, TRUE)
                            ON CONFLICT(day) DO NOTHING
                            """,
                            day_key,
                        )

                        await conn.execute(
                            """
                            INSERT INTO playtime.daily(nick, day, seconds)
                            VALUES($1, $2, $3)
                            ON CONFLICT(nick, day)
                            DO UPDATE SET seconds =
                                playtime.daily.seconds + EXCLUDED.seconds
                            """,
                            nick,
                            day_key,
                            seconds,
                        )

                        await conn.execute(
                            """
                            UPDATE playtime.totals
                            SET seconds = seconds + $1
                            WHERE nick = $2
                            """,
                            seconds,
                            nick,
                        )
                        total_added += seconds

                    cursor = segment_end

        return total_added

    async def record_success(self, online):
        now = datetime.now(timezone.utc)

        if self.last_poll is not None:
            gap = (now - self.last_poll).total_seconds()

            if 0 < gap <= self.max_gap:
                for nick in self.previous_online.intersection(online):
                    pool = get_pool()
                    async with pool.acquire() as conn:
                        exists = await conn.fetchval(
                            "SELECT 1 FROM playtime.totals WHERE nick = $1",
                            nick,
                        )
                    if exists:
                        await self._add_interval(nick, self.last_poll, now)
            else:
                await self._mark_unreliable(self.last_poll, now)

        today = now.astimezone(MSK).date().isoformat()
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO playtime.day_status(day, reliable)
                VALUES($1, TRUE)
                ON CONFLICT(day) DO NOTHING
                """,
                today,
            )

        self.previous_online = online
        self.last_poll = now

    async def record_failure(self):
        now = datetime.now(timezone.utc)

        if self.last_poll is not None:
            await self._mark_unreliable(self.last_poll, now)
        else:
            await self._mark_unreliable(now, now)

        self.previous_online.clear()
        self.last_poll = None

    async def total_seconds(self, nick):
        pool = get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT seconds FROM playtime.totals WHERE nick = $1",
                self._key(nick),
            )
        return int(value or 0)

    async def daily_seconds(self, nick, day):
        pool = get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT seconds FROM playtime.daily
                WHERE nick = $1 AND day = $2
                """,
                self._key(nick),
                day,
            )
        return int(value or 0)

    async def is_day_reliable(self, day):
        pool = get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT reliable FROM playtime.day_status WHERE day = $1",
                day,
            )
        return bool(value) if value is not None else False

    async def is_day_credited(self, day):
        pool = get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT 1 FROM playtime.credited_days WHERE day = $1",
                day,
            )
        return value is not None

    async def mark_day_credited(self, day):
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO playtime.credited_days(day, credited_at)
                VALUES($1, $2)
                ON CONFLICT(day) DO NOTHING
                """,
                day,
                datetime.now(timezone.utc).isoformat(),
            )

    async def _loop(self):
        log.info(
            "Minecraft Query tracker started: %s:%s, interval=%ss",
            MC_QUERY_HOST,
            MC_QUERY_PORT,
            MC_QUERY_INTERVAL,
        )

        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        await self._mark_unreliable(yesterday, now)

        while True:
            try:
                online = await asyncio.to_thread(self._query_players)
                await self.record_success(online)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                try:
                    await self.record_failure()
                except Exception:
                    log.exception("Failed to record Query failure")
                log.warning("Minecraft Query poll failed: %s", exc)

            await asyncio.sleep(MC_QUERY_INTERVAL)

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._loop())

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None
