"""
Core configuration and environment settings for WeriTime Staff Management Bot.
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
        logging.FileHandler("weritime_staff_bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("weritime_staff")

# ─── Основные параметры Discord ───────────────────────────
TOKEN = os.getenv("BOT_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID", "1554536535247032401") or "1554536535247032401")
WARN_NOTIFY_CHANNEL = int(os.getenv("WARN_NOTIFY_CHANNEL", "0") or "0")
STAFF_NOTIFY_CHANNEL = int(os.getenv("STAFF_NOTIFY_CHANNEL", "1554580639083397161") or "1554580639083397161")
if STAFF_NOTIFY_CHANNEL <= 0:
    STAFF_NOTIFY_CHANNEL = 1554580639083397161  # Канал «Персонал» из конфигурации WeriTime

# Existing Discord role IDs are reused where available; missing roles are created by the bot.
ROLE_IDS = {
    "creator": 1575503746547322932,
    "gl_owner": 1575503748283899966,
    "owner": 1575503746547322932,
    "gl_curator": 1575503750070804500,
    "curator": 1575503754600521820,
    "z_curator": 1575503755846230067,
    "gl_admin": 1575503758308155522,
    "admin": 1575503759927414794,
    "ml_admin": 1575503762879942686,
    "gl_moder": 1575503768315756666,
    "st_moder": 1575503770043944970,
    "moder": 1575503770043944970,
    "ml_moder": 1575503771650236518,
    "helper": 1575503773156118528,
    "project_team": 1575503774703943750,
}

# Discord hierarchy from highest to lowest. Legacy roles not listed here are left untouched.
# Permission sets avoid Administrator and grant only task-related moderation rights.
STAFF_ROLE_SPECS = [
    {"key": "owner", "name": "👑・Владелец", "color": 0xF1C40F, "permissions": "owner"},
    {"key": "gl_owner", "name": "💎・Со-Владелец", "color": 0x3498DB, "permissions": "owner"},
    {"key": "gl_curator", "name": "🌟・Гл.Куратор", "color": 0x9B59B6, "permissions": "lead"},
    {"key": "curator", "name": "🔮・Куратор", "color": 0x8E44AD, "permissions": "lead"},
    {"key": "z_curator", "name": "✨・Зам.Куратор", "color": 0xAF7AC5, "permissions": "lead"},
    {"key": "gl_admin", "name": "🛡️・Гл.Админ", "color": 0xD35400, "permissions": "admin"},
    {"key": "admin", "name": "⚔️・Админ", "color": 0xE67E22, "permissions": "admin"},
    {"key": "ml_admin", "name": "🔰・Мл.Админ", "color": 0xF39C12, "permissions": "moderator"},
    {"key": "gl_moder", "name": "🚨・Гл.Модер", "color": 0x2471A3, "permissions": "moderator"},
    {"key": "moder", "name": "🔨・Модер", "color": 0x3498DB, "permissions": "moderator"},
    {"key": "ml_moder", "name": "🧹・Мл.Модер", "color": 0x5DADE2, "permissions": "moderator"},
    {"key": "helper", "name": "🤝・Стажер", "color": 0x16A085, "permissions": "basic"},
    {"key": "project_team", "name": "🧰・Сотрудник проекта", "color": 0x7F8C8D, "permissions": "basic"},
]
SUPPORT_CATEGORY_ID = 1571519246390009866
SUPPORT_ROLE_ID = 1571553950698569819
SUPPORT_LINK_GUILD_ID = 1526050613643968522
SUPPORT_LINK_CHANNEL_ID = 1526050614436433970

# ─── Настройки Google Sheets ──────────────────────────────
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "").strip()
GOOGLE_CREDS_FILE = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", str(pathlib.Path(__file__).resolve().parents[2] / "google-sheets.json"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

# AI provider settings; keep all keys in the local .env file.
AI_PROVIDER = os.getenv("AI_PROVIDER", "openai").strip().lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROK_API_KEY = os.getenv("GROK_API_KEY", "").strip()
if AI_PROVIDER == "groq":
    AI_API_KEY = GROQ_API_KEY
    AI_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").strip().rstrip("/")
    AI_TEXT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
    AI_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", AI_TEXT_MODEL).strip()
elif AI_PROVIDER in {"grok", "xai"}:
    AI_API_KEY = GROK_API_KEY
    AI_BASE_URL = os.getenv("GROK_BASE_URL", "https://api.x.ai/v1").strip().rstrip("/")
    AI_TEXT_MODEL = os.getenv("GROK_MODEL", "grok-3-mini").strip()
    AI_VISION_MODEL = os.getenv("GROK_VISION_MODEL", AI_TEXT_MODEL).strip()
else:
    AI_API_KEY = OPENAI_API_KEY
    AI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    AI_TEXT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    AI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", AI_TEXT_MODEL).strip()

# Grok Imagine supports generated images; use it when its key exists, with OpenAI fallback.
if GROK_API_KEY:
    AI_IMAGE_API_KEY = GROK_API_KEY
    AI_IMAGE_BASE_URL = os.getenv("GROK_BASE_URL", "https://api.x.ai/v1").strip().rstrip("/")
    AI_IMAGE_MODEL = os.getenv("GROK_IMAGE_MODEL", "grok-imagine-image-2.0").strip()
    AI_IMAGE_PROVIDER = "grok"
else:
    AI_IMAGE_API_KEY = OPENAI_API_KEY
    AI_IMAGE_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    AI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1").strip()
    AI_IMAGE_PROVIDER = "openai"

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
MC_QUERY_PORT = int(os.getenv("MC_QUERY_PORT", "25696"))
MC_QUERY_INTERVAL = int(os.getenv("MC_QUERY_INTERVAL", "30"))
MC_QUERY_TIMEOUT = float(os.getenv("MC_QUERY_TIMEOUT", "5"))

# Minecraft RCON (используется отдельным сервисом аудита)
RCON_HOST = os.getenv("RCON_HOST", "m1.nexuz.ru").strip()
RCON_PORT = int(os.getenv("RCON_PORT", "25812"))
RCON_PASSWORD = os.getenv("RCON_PASSWORD", "").strip()
