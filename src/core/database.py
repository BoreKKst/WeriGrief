
import asyncpg
import logging
import os
import shutil
import subprocess
import time

from src.core.config import log

_pool: asyncpg.Pool | None = None



def _ensure_postgres_running():
    """Запускает PostgreSQL в Termux, если сервер ещё не работает."""
    prefix = os.environ.get(
        "PREFIX", "/data/data/com.termux/files/usr"
    )
    pgdata = os.path.join(prefix, "var/lib/postgresql")
    log_file = os.path.join(pgdata, "server.log")

    pg_ctl = shutil.which("pg_ctl") or os.path.join(prefix, "bin/pg_ctl")
    pg_isready = (
        shutil.which("pg_isready")
        or os.path.join(prefix, "bin/pg_isready")
    )

    if not os.path.isfile(pg_ctl):
        raise RuntimeError(f"pg_ctl не найден: {pg_ctl}")

    status = subprocess.run(
        [pg_ctl, "-D", pgdata, "status"],
        capture_output=True,
        text=True,
    )

    if status.returncode != 0:
        result = subprocess.run(
            [pg_ctl, "-D", pgdata, "-l", log_file, "start"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            status = subprocess.run(
                [pg_ctl, "-D", pgdata, "status"],
                capture_output=True,
                text=True,
            )
            if status.returncode != 0:
                raise RuntimeError(
                    "Не удалось запустить PostgreSQL: "
                    + (result.stderr or result.stdout)
                )

    if not os.path.isfile(pg_isready):
        raise RuntimeError(f"pg_isready не найден: {pg_isready}")

    for _ in range(30):
        ready = subprocess.run(
            [pg_isready, "-h", "127.0.0.1", "-p", "5432"],
            capture_output=True,
            text=True,
        )
        if ready.returncode == 0:
            return
        time.sleep(0.5)

    raise RuntimeError("PostgreSQL не стал готов принимать подключения")


async def init_db():
    """Создаёт общий пул подключений PostgreSQL."""
    global _pool

    if _pool is not None:
        return _pool

    _ensure_postgres_running()
    _pool = await asyncpg.create_pool(
        host="127.0.0.1",
        port=5432,
        database="werigrief",
        user="werigrief",
        min_size=1,
        max_size=5,
        command_timeout=30,
    )

    async with _pool.acquire() as conn:
        await conn.fetchval("SELECT 1")

    log.info("[PostgreSQL] Connection pool initialized")
    return _pool


def get_pool() -> asyncpg.Pool:
    """Возвращает общий пул подключений."""
    if _pool is None:
        raise RuntimeError("PostgreSQL pool is not initialized")
    return _pool


async def close_db():
    """Корректно закрывает пул подключений."""
    global _pool

    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("[PostgreSQL] Connection pool closed")


async def check_db() -> bool:
    """Проверяет доступность PostgreSQL."""
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval("SELECT 1") == 1
    except Exception:
        log.exception("[PostgreSQL] Connection check failed")
        return False
