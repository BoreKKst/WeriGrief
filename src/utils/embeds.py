"""
Discord Embed formatting utilities for staff notifications and reports.
"""

from datetime import datetime, timezone
import discord
from src.core.config import log
from src.services.sheets_api import resolve_email_to_nick, normalize_date_str


class EmbedBuilder:
    """Генератор форматированных Embed-сообщений для Discord."""

    @staticmethod
    def build_warn_embed(nick: str, date: str, reason: str, issued_by: str, warn_count: int) -> discord.Embed:
        """Создает Embed о выданном предупреждении."""
        colors = {1: 0xFFA500, 2: 0xFF6600, 3: 0xFF0000}
        color = colors.get(warn_count, 0xFF0000)
        clean_date = normalize_date_str(date)

        embed = discord.Embed(
            title="⚠️ Предупреждение выдано",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="👤 Сотрудник", value=f"`{nick}`", inline=True)
        embed.add_field(name="📅 Дата", value=f"`{clean_date}`", inline=True)
        embed.add_field(name="🔢 Всего предупреждений", value=f"**{warn_count}**", inline=True)
        embed.add_field(name="📋 Причина", value=reason, inline=False)
        embed.add_field(name="👮 Выдал", value=f"`{issued_by}`", inline=True)

        if warn_count >= 3:
            embed.add_field(
                name="🚨 КРИТИЧЕСКИЙ ПОРОГ (3+)",
                value="Сотрудник набрал **3+ предупреждений**. Назначена роль `⚠️ На испытании`.",
                inline=False,
            )
        embed.set_footer(text="WeriTime Staff System • Google Sheets Sync")
        return embed

    @staticmethod
    def build_player_warns_cleared_embed(
        nick: str, removed_count: int, current_count: int, removed_items: list[dict], role_removed: bool
    ) -> discord.Embed:
        """Создает Embed об аннулировании предупреждений."""
        embed = discord.Embed(
            title="✅ Снятие предупреждений сотрудника",
            color=0x2ECC71,
            description=f"Администрация сняла предупреждения с сотрудника `{nick}`.",
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="👤 Сотрудник", value=f"`{nick}`", inline=True)
        embed.add_field(name="📉 Снято", value=f"**{removed_count}** шт.", inline=True)
        embed.add_field(name="🔢 Текущий остаток", value=f"**{current_count}** шт.", inline=True)

        if removed_items:
            reasons_text = "\n".join(
                [f"• `{normalize_date_str(item.get('date', '—'))}`: {item.get('reason', '—')}" for item in removed_items[:5]]
            )
            embed.add_field(name="📋 Аннулированные нарушения", value=reasons_text, inline=False)

        if role_removed:
            embed.add_field(
                name="🎉 Испытательный срок завершен",
                value="Количество предупреждений стало меньше 3. Роль `⚠️ На испытании` автоматически снята.",
                inline=False,
            )
        embed.set_footer(text="WeriTime Staff System • Google Sheets Sync")
        return embed

    @staticmethod
    def build_monthly_reset_embed(current_month_name: str) -> discord.Embed:
        """Создает Embed о ежемесячном сбросе предупреждений 1-го числа."""
        embed = discord.Embed(
            title="🔄 Ежемесячный сброс предупреждений!",
            color=0x3498DB,
            description=(
                f"**Наступил новый месяц ({current_month_name})!**\n\n"
                f"• Таблица «Учет предупреждений» очищена (заголовки столбцов сохранены).\n"
                f"• В листе «Списки» счетчики предупреждений сброшены на **0**.\n"
                f"• Роли `⚠️ На испытании` сняты со всех сотрудников.\n\n"
                f"Желаем всему составу администрации успешной и продуктивной работы!"
            ),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="WeriTime Staff System • Автоматический сброс 1-го числа")
        return embed

    @staticmethod
    def build_staff_appointed_embed(
        nick: str,
        position: str,
        discord_name: str = "",
        appointed_by: str = "",
        email: str = "",
        comment: str = "",
        roles_granted: list[str] = None,
        roles_blocked: list[str] = None,
        mc_group: str = "",
        mc_granted: bool = False,
    ) -> discord.Embed:
        """Создает Embed о новом назначении в персонал."""
        is_leadership_role = any(
            term in position.lower() for term in ["Creator", "Owners", "куратор", "администратор", "руковод", "глава"]
        )
        color = 0xF1C40F if is_leadership_role else 0x2ECC71
        title = "👑 Назначение в Руководство («Вышка»)" if is_leadership_role else "🛡️ Новое назначение в персонал!"

        display_nick = resolve_email_to_nick(nick)
        display_appointer = resolve_email_to_nick(appointed_by) if appointed_by else "Руководство проекта"

        embed = discord.Embed(
            title=title,
            color=color,
            description=f"В состав администрации проекта назначен сотрудник `{display_nick}`.",
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="👤 Никнейм", value=f"`{display_nick}`", inline=True)
        embed.add_field(name="📌 Должность", value=f"**{position}**", inline=True)
        embed.add_field(name="👮 Назначил", value=f"`{display_appointer}`", inline=True)

        if discord_name:
            embed.add_field(name="💬 Discord", value=f"`{discord_name}`", inline=True)

        if roles_granted:
            roles_text = ", ".join([f"`{r}`" for r in roles_granted])
            embed.add_field(name="🎭 Роли в Discord", value=f"✅ Выданы: {roles_text}", inline=False)
        elif not roles_blocked:
            embed.add_field(name="🎭 Роли в Discord", value="Роли персонала уже назначены / синхронизированы", inline=False)

        if roles_blocked:
            blocked_text = ", ".join([f"`{r}`" for r in roles_blocked])
            embed.add_field(
                name="⚠️ Не удалось выдать роли (иерархия Discord)",
                value=(
                    f"Роли: {blocked_text}\n"
                    f"ℹ️ *Роль бота стоит в списке ниже этих ролей. Поднимите роль бота **WeriTime** в Настройки сервера → Роли выше этих ролей.*"
                ),
                inline=False,
            )

        if mc_group:
            mc_status = (
                f"✅ Pterodactyl API принял команду `lp user <nick> parent set {mc_group}`. "
                "Применение группы на Minecraft-сервере отдельно не подтверждено."
                if mc_granted else
                f"⚠️ Не удалось подтвердить отправку команды для группы `{mc_group}` через Pterodactyl API."
            )
            embed.add_field(name="🎮 Права в Minecraft", value=mc_status, inline=False)

        if comment:
            embed.add_field(name="📝 Обязанности / Заметки", value=comment[:1000], inline=False)

        embed.set_footer(text="WeriTime Staff Management • Уведомления о персонале")
        return embed

    @staticmethod
    def build_staff_promoted_embed(
        nick: str,
        old_pos: str,
        new_pos: str,
        promoted_by: str = "",
        discord_name: str = "",
        email: str = "",
        roles_granted: list[str] = None,
        roles_blocked: list[str] = None,
        mc_group: str = "",
        mc_granted: bool = False,
    ) -> discord.Embed:
        """Создает Embed о переводе / повышении сотрудника."""
        display_nick = resolve_email_to_nick(nick)
        display_promoter = resolve_email_to_nick(promoted_by) if promoted_by else "Руководство проекта"

        embed = discord.Embed(
            title="📈 Изменение должности в персонале!",
            color=0xF39C12,
            description=f"Сотрудник `{display_nick}` переведен на новую должность.",
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="👤 Сотрудник", value=f"`{display_nick}`", inline=True)
        embed.add_field(name="🔄 Изменение", value=f"`{old_pos}` ➔ **{new_pos}**", inline=True)
        embed.add_field(name="👮 Изменил должность", value=f"`{display_promoter}`", inline=True)

        if discord_name:
            embed.add_field(name="💬 Discord", value=f"`{discord_name}`", inline=True)

        if roles_granted:
            roles_text = ", ".join([f"`{r}`" for r in roles_granted])
            embed.add_field(name="🎭 Роли в Discord", value=f"✅ Обновлены: {roles_text}", inline=False)

        if roles_blocked:
            blocked_text = ", ".join([f"`{r}`" for r in roles_blocked])
            embed.add_field(
                name="⚠️ Не удалось выдать роли (иерархия Discord)",
                value=(
                    f"Роли: {blocked_text}\n"
                    f"ℹ️ *Роль бота стоит в списке ниже этих ролей. Поднимите роль бота **WeriTime** в Настройки сервера → Роли выше этих ролей.*"
                ),
                inline=False,
            )

        if mc_group:
            mc_status = f"✅ Установлена группа `{mc_group}` (LuckPerms)" if mc_granted else f"⚡ Команда `lp parent set {mc_group}` отправлена"
            embed.add_field(name="🎮 Права в Minecraft", value=mc_status, inline=False)

        embed.set_footer(text="WeriTime Staff Management • Уведомления о персонале")
        return embed

    @staticmethod
    def build_staff_removed_embed(
        nick: str,
        position: str,
        email: str = "",
        warns_cleared: int = 0,
        removed_by: str = "",
        reason: str = "",
    ) -> discord.Embed:
        """Создает Embed об увольнении/снятии сотрудника с должности."""
        display_nick = resolve_email_to_nick(nick)
        display_remover = resolve_email_to_nick(removed_by) if removed_by else "Руководство проекта"

        embed = discord.Embed(
            title="👋 Снятие с должности персонала («Админ пока»)",
            color=0xE74C3C,
            description=f"Сотрудник `{display_nick}` покинул состав администрации проекта.",
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="👤 Никнейм", value=f"`{display_nick}`", inline=True)
        embed.add_field(name="📌 Бывшая должность", value=f"`{position}`", inline=True)
        embed.add_field(name="👮 Снял", value=f"`{display_remover}`", inline=True)

        if reason:
            embed.add_field(name="📋 Причина", value=reason, inline=False)

        clean_note = (
            f"Автоматически аннулировано **{warns_cleared}** варнов из базы"
            if warns_cleared > 0
            else "Предупреждений в базе не было"
        )
        embed.add_field(name="🧹 Очистка предупреждений", value=clean_note, inline=False)
        embed.add_field(name="📁 Журнал учета", value="Запись о снятии занесена в реестр уволенного персонала", inline=False)
        embed.set_footer(text="WeriTime Staff Management • Учет снятых сотрудников")
        return embed

    @staticmethod
    async def send_dismissal_dm(
        member: discord.Member,
        nick: str,
        position: str,
        removed_by: str = "",
        reason: str = "",
        warnings_history: list[dict] | None = None,
        automatic: bool = False,
        role_status: str = "",
        minecraft_status: str = "",
    ) -> bool:
        """Подробное личное уведомление о снятии сотрудника."""
        if not member:
            return False

        display_remover = resolve_email_to_nick(removed_by) if removed_by else "Руководство проекта"
        embed = discord.Embed(
            title="⚠️ Автоматическое снятие с должности" if automatic else "👋 Уведомление о снятии с должности",
            color=0xE74C3C,
            description=f"Здравствуйте, **{nick}**.\n\nВы сняты с должности **{position}** на проекте **✨WeriTime✨**.",
            timestamp=datetime.now(timezone.utc),
        )

        if automatic:
            embed.add_field(
                name="📊 Основание снятия",
                value="Вы получили **3 предупреждения**. Согласно правилам проекта, при достижении трёх предупреждений сотрудник автоматически снимается с должности.",
                inline=False,
            )
        if reason:
            embed.add_field(name="📋 Причина", value=str(reason)[:1024], inline=False)

        embed.add_field(name="👮 Кто снял", value=f"`{display_remover}`", inline=True)

        history = warnings_history or []
        if history:
            lines = [
                f"**{i}. {item.get('date', '—')}** — {item.get('reason', 'Причина не указана')}\nВыдал: `{item.get('issued_by', 'Неизвестно')}`"
                for i, item in enumerate(history, 1)
            ]
            history_text = "\n\n".join(lines)
            if len(history_text) > 1024:
                history_text = history_text[:990] + "\n… Полная история сохранена в журнале."
        else:
            history_text = "Подробные записи предупреждений не найдены."

        embed.add_field(name=f"📚 История предупреждений ({len(history)})", value=history_text, inline=False)

        if role_status:
            embed.add_field(name="🎭 Discord-роли", value=role_status[:1024], inline=False)
        if minecraft_status:
            embed.add_field(name="⛏️ Minecraft / LuckPerms", value=minecraft_status[:1024], inline=False)

        embed.add_field(
            name="ℹ️ Информация",
            value="Снятие касается должностных полномочий. Донатные роли и группы не должны затрагиваться.",
            inline=False,
        )
        embed.set_footer(text="WeriTime Staff System • Личное уведомление")

        try:
            await member.send(embed=embed)
            log.info("Sent dismissal DM to %s (%s)", member, nick)
            return True
        except discord.errors.Forbidden:
            log.warning("Could not send dismissal DM to %s: direct messages are closed", member)
            return False
        except Exception:
            log.exception("Error sending dismissal DM to %s (%s)", member, nick)
            return False

