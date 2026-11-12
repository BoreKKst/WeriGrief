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
STAFF_NOTIFY_CHANNEL = int(os.getenv("STAFF_NOTIFY_CHANNEL", "0") or "0")

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

# ─── Статический реестр персонала (Static Staff Registry) ─
STAFF_REGISTRY = {
    # Minecraft nickname -> Discord account mapping
    "borekkst": {"discord_name": "borekkst", "discord_id": 1374053953301385308},
    "fenix_763": {"discord_name": "fenix_763", "discord_id": 1279748957358002276},
    "lattexfifka": {"discord_name": "he1nyaqprfff", "discord_id": 1467862182929895585},
    "devchick": {"discord_name": "devchick_official", "discord_id": 1359612735817515272},
}

# ─── Права и ключевые слова руководства ────────────────────
LEADERSHIP_ROLES = ["creator", "gl.owner", "owner", "gl.curator", "curator", "z.curator", "руковод"]
ADMIN_KEYWORDS = [
    "creator", "gl.owner", "owner", "gl.curator", "curator", "z.curator",
    "gl.admin", "admin", "gl.moder", "st.moder", "moder", "ml.moder",
]
LEADERSHIP_IDS = set()  # Add trusted Discord user IDs here only if needed.

