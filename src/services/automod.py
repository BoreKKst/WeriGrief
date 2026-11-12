"""
Automoderation service: link detection, domain whitelisting, and exemption checking.
"""

import re
from urllib.parse import urlparse
import discord
from src.core.config import LEADERSHIP_IDS
from src.services.permissions import STAFF_ROLE_IDS, StaffRoleManager
from src.ui.views import SUPPORT_CATEGORY_ID

LINK_FILTER_REGEX = re.compile(
    r"(https?://[^\s<]+)|"
    r"(\b(?:[a-zA-Z0-9-]+\.)+(?:com|ru|org|net|xyz|me|io|gg|su|to|co|app|live|shop|online|site|pro|info|fun|link|top|dev|club|cc|space|cloud|biz|games|store|tech|by|kz|ua|ly|gd|is|ms|pw)\b(?:/[^\s<]*)?)",
    re.IGNORECASE,
)

WHITELISTED_LINK_DOMAINS = {
    "tenor.com",
    "giphy.com",
}


class AutomodService:
    """Сервис автомодерации сообщений и защиты от рекламы."""

    def __init__(self, staff_resolver):
        self.staff_resolver = staff_resolver

    @staticmethod
    def _raw_check_text_links(text: str, guild_id: int) -> bool:
        """Проверяет наличие сторонних ссылок в тексте."""
        matches = LINK_FILTER_REGEX.findall(text)
        if not matches:
            return False
        for m in matches:
            raw_url = m[0] or m[1]
            raw_url = raw_url.strip(".,!?:;\"'()[]{}")
            if not raw_url:
                continue
            to_parse = raw_url if "://" in raw_url else "http://" + raw_url
            try:
                parsed = urlparse(to_parse)
                host = (parsed.netloc or "").lower().split(":")[0]
                if not host:
                    continue
                # Разрешенные домены проекта и гифок
                if any(host == d or host.endswith("." + d) for d in WHITELISTED_LINK_DOMAINS):
                    continue
                # Внутренние ссылки Discord на каналы текущего сервера
                if host in ("discord.com", "discordapp.com") and parsed.path.startswith(f"/channels/{guild_id}/"):
                    continue
                return True
            except Exception:
                return True
        return False

    @classmethod
    def contains_forbidden_link(cls, text: str, guild_id: int) -> bool:
        """Проверяет текст на ссылки, включая обфускацию пробелами (напр. 't . me')."""
        if not text:
            return False
        if cls._raw_check_text_links(text, guild_id):
            return True
        cleaned_spaces = re.sub(r"\s*([./:])\s*", r"\1", text)
        if cleaned_spaces != text and cls._raw_check_text_links(cleaned_spaces, guild_id):
            return True
        return False

    @staticmethod
    def is_ticket_or_support_channel(channel: discord.abc.GuildChannel | None) -> bool:
        """Проверяет, является ли канал тикетом/поддержкой (где ссылки-доказательства разрешены)."""
        if not channel:
            return False

        parent = getattr(channel, "parent", None)
        if parent and AutomodService.is_ticket_or_support_channel(parent):
            return True

        category_id = getattr(channel, "category_id", None)
        if category_id == SUPPORT_CATEGORY_ID:
            return True

        name = getattr(channel, "name", "").lower()
        if name.startswith(("репорт-", "ticket-", "тикет-", "жалоба-", "баг-", "заявка-", "донат-", "поддержка-")):
            return True

        topic = getattr(channel, "topic", "") or ""
        if "ID:" in topic or "Тикет" in topic or "ticket" in topic.lower():
            return True

        return False

    def is_link_filter_exempt(self, member: discord.Member, channel: discord.abc.GuildChannel | None) -> bool:
        """Проверяет освобождение пользователя от фильтра ссылок (персонал, боты, администрация)."""
        if not isinstance(member, discord.Member):
            return True
        if member.bot:
            return True

        guild = member.guild
        if member.id == guild.owner_id or member.id in LEADERSHIP_IDS:
            return True
        if member.guild_permissions.administrator or member.guild_permissions.manage_messages:
            return True

        for role in member.roles:
            if role.id in STAFF_ROLE_IDS or StaffRoleManager.is_staff_role(role):
                return True

        if self.staff_resolver.is_admin_or_higher(member, guild) or self.staff_resolver.is_leadership(member, guild):
            return True

        if self.is_ticket_or_support_channel(channel):
            return True

        return False

