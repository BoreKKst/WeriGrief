"""
Core configuration and environment settings for WeriGrief Staff Management Bot.
"""

import os
import pathlib
import logging
from dotenv import load_dotenv

# Загружаем переменные окружения из корня проекта или выше
load_dotenv(pathlib.Path(__file__).parent.parent.parent / ".env", override=True)
load_dotenv(override=True)

# ─── Логирование ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("werigrief_staff_bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("werigrief_staff")

# ─── Основные параметры Discord ───────────────────────────
TOKEN = os.getenv("BOT_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID", "1554536535247032401") or "1554536535247032401")
WARN_NOTIFY_CHANNEL = int(os.getenv("WARN_NOTIFY_CHANNEL", "0") or "0")
STAFF_NOTIFY_CHANNEL = int(os.getenv("STAFF_NOTIFY_CHANNEL", "1554580639083397161") or "1554580639083397161")
if STAFF_NOTIFY_CHANNEL <= 0:
    STAFF_NOTIFY_CHANNEL = 1554580639083397161  # Канал «Персонал» из конфигурации WeriGrief

# Discord IDs extracted from the current WeriGrief archive (no IDs inferred).
ROLE_IDS = {
    "creator": 1554582347872079914,
    "gl_owner": 1569338896448356372,
    "owner": 1554582667000021125,
    "gl_curator": 1554582767965311068,
    "curator": 1569194858982285433,
    "z_curator": 1569195073814405140,
    "gl_admin": 1554582998928859176,
    "admin": 1554583247902605312,
    "gl_moder": 1554583311568085113,
    "st_moder": 1570415032091807815,
    "moder": 1554583443650912409,
    "ml_moder": 1569198191541362841,
    "ml_admin": 1571090715386183692,
    "helper": 1554583527486390272,
    "project_team": 1564330724482355330,
}
SUPPORT_CATEGORY_ID = 1526050614151348314
SUPPORT_LINK_GUILD_ID = 1526050613643968522
SUPPORT_LINK_CHANNEL_ID = 1526050614436433970

# ─── Настройки Google Sheets ──────────────────────────────
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "").strip()
GOOGLE_CREDS_FILE = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/home/container/google-sheets.json")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

# ─── Интеграция с Pterodactyl & Minecraft ──────────────────
PTERO_BASE_URL = os.getenv("PTERO_BASE_URL", "https://mgr.nexuz.ru")
PTERO_API_KEY = os.getenv("PTERO_API_KEY", "").strip()
PTERO_SERVER_IDS = [
    s.strip() for s in os.getenv("PTERO_SERVER_IDS", "83bed7ed").split(",") if s.strip()
]
MAIN_MC_SERVER_ID = os.getenv("MAIN_MC_SERVER_ID", "83bed7ed")
GAME_SERVERS = [s.strip() for s in os.getenv("GAME_SERVERS", "83bed7ed").split(",") if s.strip()]
DAILY_NORM_HOURS = float(os.getenv("DAILY_NORM_HOURS", "1.0"))
MC_STATS_DIRECTORY = os.getenv("MC_STATS_DIRECTORY", "world/stats").strip().strip("/")

# ─── Статический реестр персонала (Static Staff Registry) ─
STAFF_REGISTRY = {
    # Minecraft nickname -> Discord account mapping
    "borekkst": {"discord_name": "borekkst", "discord_id": 1374053953301385308},
    "fenix_763": {"discord_name": "fenix_763", "discord_id": 1279748957358002276},
    "lattexfifka": {"discord_name": "he1nyaqprfff", "discord_id": 1467862182929895585},
    "devchick": {"discord_name": "devchick_official", "discord_id": 1359612735817515272},
}

# ─── Права и ключевые слова руководства ────────────────────
LEADERSHIP_ROLES = [
    "creator", "gl.owner", "owner", "d.owner", "gl.curator", "curator", "z.curator",
]
ADMIN_KEYWORDS = [
    "creator", "gl.owner", "owner", "d.owner", "gl.curator", "curator", "z.curator",
    "gl.admin", "admin", "ml.admin", "d.admin",
    "gl.moder", "st.moder", "moder", "ml.moder", "d.moder",
]
# Прямой доступ к кадровым командам для владельцев проекта.
LEADERSHIP_IDS = {
    1374053953301385308,  # BoreKKst
    1279748957358002276,  # fenix_763
}


# Minecraft Query — сбор подтверждённого онлайна
MC_QUERY_HOST = os.getenv("MC_QUERY_HOST", "m1.nexuz.ru").strip()
MC_QUERY_PORT = int(os.getenv("MC_QUERY_PORT", "25737"))
MC_QUERY_INTERVAL = int(os.getenv("MC_QUERY_INTERVAL", "30"))
MC_QUERY_TIMEOUT = float(os.getenv("MC_QUERY_TIMEOUT", "5"))
