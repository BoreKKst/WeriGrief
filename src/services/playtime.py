"""
Minecraft Query playtime tracking and daily staff norm.
PostgreSQL stores confirmed online time; Google Sheets stores lifetime hours.
"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import discord

from src.core.config import (
    log,
    DAILY_NORM_HOURS,
    WARN_NOTIFY_CHANNEL,
    STAFF_NOTIFY_CHANNEL,
)
from src.services.query_tracker import QueryPlaytimeStore
from src.utils.embeds import EmbedBuilder
from src.services.permissions import MinecraftPermissionsManager
from src.services.auto_dismissal import auto_dismiss_staff

MSK = ZoneInfo("Europe/Moscow")


class PlaytimeTracker:
    """Query-based staff playtime tracker."""

    def __init__(self, sheets_monitor, staff_resolver):
        self.sheets_monitor = sheets_monitor
        self.staff_resolver = staff_resolver
        self.query_store = QueryPlaytimeStore()

    @staticmethod
    def format_hours_delta(hours: float) -> str:
        minutes = int(round(max(0.0, hours) * 60))
        h, m = divmod(minutes, 60)
        if h and m:
            return f"{h}ч {m}м"
        if h:
            return f"{h}ч"
        return f"{m}м"

    def is_norm_exempt(self, position: str, mc_nick: str = "") -> bool:
        nick = mc_nick.strip().casefold()
        nick_no_us = nick.replace("_", "")

        if self.sheets_monitor:
            # Ручное включение нормы имеет наивысший приоритет.
            if (
                self.sheets_monitor.is_custom_required(nick)
                or self.sheets_monitor.is_custom_required(nick_no_us)
            ):
                return False

            # Ручное освобождение имеет приоритет над должностью.
            if (
                self.sheets_monitor.is_custom_exempt(nick)
                or self.sheets_monitor.is_custom_exempt(nick_no_us)
            ):
                return True

        pos = str(position or "").casefold()
        keywords = (
            "creator", "creators",
            "owner", "owners",
            "curator", "curators",
            "создатель", "создатели",
            "Creator", "владельцы",
            "куратор", "кураторы",
            "руковод",
        )
        return any(word in pos for word in keywords)

    async def start_query_tracking(self):
        if self.sheets_monitor:
            for staff in self.sheets_monitor.get_staff_list():
                nick = staff.get("nick")
                if nick:
                    await self.query_store.ensure_staff(
                        nick, staff.get("hours", 0)
                    )
        self.query_store.start()
        log.info("Query playtime tracking started")

    async def stop_query_tracking(self):
        await self.query_store.stop()

    async def fetch_playtime_for_staff(
        self, nicks: list[str], flush_save: bool = False
    ) -> dict:
        """Compatibility method: returns PostgreSQL lifetime totals."""
        result = {}
        for nick in nicks:
            if self.sheets_monitor:
                match = next(
                    (
                        s for s in self.sheets_monitor.get_staff_list()
                        if s.get("nick", "").casefold() == nick.casefold()
                    ),
                    None,
                )
                if match:
                    await self.query_store.ensure_staff(
                        nick, match.get("hours", 0)
                    )

            hours = await self.query_store.total_seconds(nick) / 3600
            result[nick.casefold()] = {
                "hours": hours,
                "hours_str": self.format_hours_delta(hours),
            }
        return result

    async def get_staff_norm_info(self, mc_nick: str) -> dict | None:
        if not self.sheets_monitor:
            return None

        staff_list = self.sheets_monitor.get_staff_list()
        target = mc_nick.strip().casefold()
        matched = None

        for staff in staff_list:
            nick = str(staff.get("nick", "")).strip()
            discord_name = str(staff.get("discord", "")).strip().casefold()
            if nick and nick.casefold() == target:
                matched = staff
                break
            if discord_name and (
                discord_name == target
                or discord_name.replace("_", "") == target.replace("_", "")
            ):
                matched = staff
                break

        if not matched:
            return None

        nick = matched["nick"]
        position = matched.get("position", "Сотрудник")
        await self.query_store.ensure_staff(nick, matched.get("hours", 0))

        now = datetime.now(MSK)
        day = now.strftime("%Y-%m-%d")
        reliable = await self.query_store.is_day_reliable(day)

        total_hours = await self.query_store.total_seconds(nick) / 3600
        today_hours = await self.query_store.daily_seconds(nick, day) / 3600

        appointed_at = str(matched.get("appointed_at", "")).strip()[:10]
        norm_not_started = bool(appointed_at and appointed_at == day)

        norm_met = (
            False if norm_not_started
            else reliable and today_hours >= DAILY_NORM_HOURS
        )
        remaining = (
            0.0 if norm_not_started
            else max(0.0, DAILY_NORM_HOURS - today_hours)
        )
        percent = min(
            100,
            int(today_hours / DAILY_NORM_HOURS * 100)
            if DAILY_NORM_HOURS > 0 else 100,
        )
        filled = min(10, percent // 10)

        return {
            "nick": nick,
            "position": position,
            "sheet_hours": matched.get("hours", "0"),
            "warn_count": matched.get("warns", "0"),
            "is_exempt": self.is_norm_exempt(position, nick),
            "norm_not_started": norm_not_started,
            "lifetime_hours": total_hours,
            "lifetime_str": self.format_hours_delta(total_hours),
            "today_delta": today_hours,
            "today_str": self.format_hours_delta(today_hours),
            "norm_met": norm_met,
            "daily_reliable": reliable,
            "remaining_hours": remaining,
            "remaining_str": self.format_hours_delta(remaining),
            "percent": percent,
            "progress_bar": f"[{'█' * filled}{'░' * (10-filled)}]",
        }

    async def send_norm_dm(
            self,
            member: discord.Member,
            nick: str,
            position: str,
            hours_str: str,
            total_hours: float,
            norm_met: bool,
            is_exempt: bool,
            total_warns: int = 0,
            warn_issued: bool = False,
        ) -> bool:
            """Отправляет личное сообщение сотруднику с информацией о выполнении нормы."""
            if not member:
                return False

            date_str = (datetime.now(ZoneInfo("Europe/Moscow"))).strftime("%d.%m.%Y")

            if is_exempt:
                embed = discord.Embed(
                    title="ℹ️ Суточный отчет по онлайну",
                    color=0x3498DB,
                    description=(
                        f"Здравствуйте, **{nick}**!\n\n"
                        f"Ваш онлайн на сервере за прошедшие сутки: **{hours_str}**.\n"
                        f"📌 **Должность:** {position}\n"
                        f"📅 **Дата:** `{date_str}`\n\n"
                        f"ℹ️ *Для руководящего состава обязательная суточная норма не установлена.*"
                    ),
                    timestamp=datetime.now(timezone.utc),
                )
            elif norm_met:
                embed = discord.Embed(
                    title="✅ Суточная норма онлайна выполнена!",
                    color=0x2ECC71,
                    description=(
                        f"Здравствуйте, **{nick}**!\n\n"
                        f"Вы успешно выполнили обязательную норму онлайна (**1 час**).\n\n"
                        f"⏱ **Отыграно времени:** **{hours_str}** ({total_hours:.1f} ч.)\n"
                        f"📌 **Должность:** {position}\n"
                        f"📅 **Дата:** `{date_str}`\n\n"
                        f"Спасибо за активную работу и вклад в развитие проекта **✨WeriTime✨**! 🚀"
                    ),
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                warn_field = (
                    f"\n🚨 **Вам автоматически выдано предупреждение.**\n🔢 **Всего предупреждений:** **{total_warns}**"
                    if warn_issued
                    else "\n⚠️ Норма не выполнена."
                )
                embed = discord.Embed(
                    title="⚠️ Невыполнение суточной нормы онлайна!",
                    color=0xE74C3C,
                    description=(
                        f"Здравствуйте, **{nick}**!\n\n"
                        f"Вы **не выполнили** обязательную норму онлайна (**1 час**).\n\n"
                        f"⏱ **Отыграно времени:** **{hours_str}** (норма: 1ч 00м)\n"
                        f"📌 **Должность:** {position}\n"
                        f"📅 **Дата:** `{date_str}`\n"
                        f"{warn_field}\n\n"
                        f"*(При наборе 3-х предупреждений автоматически выдается роль испытательного срока).*\n"
                        f"Пожалуйста, соблюдайте нормы активности на проекте **✨WeriTime✨**."
                    ),
                    timestamp=datetime.now(timezone.utc),
                )
            embed.set_footer(text="WeriTime Staff System • Контроль онлайна")

            try:
                await member.send(embed=embed)
                log.info(f"✅ Sent daily norm DM to {member} ({nick}) [Met: {norm_met}, Exempt: {is_exempt}]")
                return True
            except discord.errors.Forbidden:
                log.warning(f"⚠️ Could not send norm DM to {member} ({nick}): DMs closed")
                return False
            except Exception as e:
                log.warning(f"Failed to send norm DM to {member} ({nick}): {e}")
                return False

    async def process_daily_playtime_and_norms(
        self,
        guild: discord.Guild,
        dry_run: bool = False,
        is_midnight: bool = False,
        send_summary: bool = True,
        issue_warnings: bool = False,
    ) -> dict:
        if not self.sheets_monitor:
            log.error("Sheets monitor not ready")
            return {"error": "Sheets monitor not ready"}

        staff_list = [
            s for s in self.sheets_monitor.get_staff_list()
            if s.get("nick")
        ]
        if not staff_list:
            return {"error": "No staff found"}

        now = datetime.now(MSK)
        # При ручной выдаче проверяем последние закрытые сутки.
        target_date = (
            now.date() - timedelta(days=1)
            if is_midnight or (dry_run and issue_warnings)
            else now.date()
        )
        day = target_date.strftime("%Y-%m-%d")
        date_str = target_date.strftime("%d.%m.%Y")

        reliable = await self.query_store.is_day_reliable(day)
        already_credited = (
            is_midnight and await self.query_store.is_day_credited(day)
        )
        should_credit = (
            is_midnight and not dry_run and not already_credited
        )

        met_list = []
        failed_list = []
        exempt_list = []
        vacation_list = []
        newcomer_list = []
        hours_delta_map = {}

        # Сначала собираем всё подтверждённое время.
        # Даже при ненадёжном дне оно будет добавлено в общий онлайн.
        for staff in staff_list:
            nick = staff["nick"]
            position = staff.get("position", "Сотрудник")
            await self.query_store.ensure_staff(nick, staff.get("hours", 0))

            seconds = await self.query_store.daily_seconds(nick, day)
            hours = seconds / 3600

            if hours > 0:
                hours_delta_map[nick.casefold()] = hours

            # В день назначения новичок ещё не обязан выполнять норму.
            appointed_at = str(staff.get("appointed_at", "")).strip()[:10]
            if appointed_at and appointed_at == day:
                newcomer_list.append(
                    (nick, position, self.format_hours_delta(hours), hours)
                )
                continue

            if self.sheets_monitor.is_on_vacation(nick):
                vacation_list.append(
                    (nick, position, self.format_hours_delta(hours), hours)
                )
                continue

            if self.is_norm_exempt(position, nick):
                exempt_list.append(
                    (nick, position, self.format_hours_delta(hours), hours)
                )
                continue

            hours_str = self.format_hours_delta(hours)
            if hours >= DAILY_NORM_HOURS:
                met_list.append((nick, position, hours_str, hours))
            else:
                failed_list.append(
                    (nick, position, hours_str, hours, 0)
                )

        # Сначала начисляем часы и проверяем результат.
        # Только после успешного начисления разрешаем предупреждения.
        if should_credit:
            if hours_delta_map:
                result = self.sheets_monitor.increment_staff_hours(
                    hours_delta_map
                )
                missing = set(hours_delta_map) - set(result)
                if missing:
                    log.error(
                        "Sheets hours update incomplete; missing: %s",
                        sorted(missing),
                    )
                    return {
                        "error": "Не удалось подтвердить начисление часов",
                        "missing": sorted(missing),
                    }

            await self.query_store.mark_day_credited(day)

        actions_allowed = should_credit
        # Личные сообщения и начисление часов — только при первом закрытии дня.
        # Автоматическая выдача в полночь или подтверждённая ручная выдача.
        warnings_allowed = (
            (is_midnight and not dry_run)
            or (dry_run and issue_warnings)
        )

        warn_channel = (
            guild.get_channel(WARN_NOTIFY_CHANNEL)
            if guild and WARN_NOTIFY_CHANNEL else None
        )
        staff_channel = (
            guild.get_channel(STAFF_NOTIFY_CHANNEL)
            if guild and STAFF_NOTIFY_CHANNEL else None
        )

        if (
            send_summary and guild
            and staff_channel is None and STAFF_NOTIFY_CHANNEL
        ):
            try:
                staff_channel = await guild.fetch_channel(
                    STAFF_NOTIFY_CHANNEL
                )
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ) as exc:
                log.error("Cannot fetch staff report channel: %s", exc)

        # Предупреждения только при автоматическом закрытии дня в 00:00 МСК.
        if warnings_allowed:
            for item in failed_list:
                nick, position, hours_str, hours, _ = item
                member = (
                    self.staff_resolver.find_member_by_mc_nick(guild, nick)
                    if guild else None
                )
                reason = (
                    f"Невыполнение суточной нормы онлайна "
                    f"(наиграно: {hours_str} из "
                    f"{DAILY_NORM_HOURS:g}ч)"
                )
                total_warns, warning_created, warning_ok = (
                    self.sheets_monitor.add_warning_once(
                        nick, date_str, reason,
                        "Система контроля нормы",
                    )
                )

                if not warning_ok:
                    log.error(
                        "Automatic warning not confirmed for %s on %s",
                        nick, date_str,
                    )
                    return {
                        "error": f"Не удалось подтвердить предупреждение для {nick}",
                    }
                item_index = failed_list.index(item)
                failed_list[item_index] = (
                    nick, position, hours_str, hours, total_warns
                )


                if warning_created and warn_channel and total_warns > 0:
                    embed = EmbedBuilder.build_warn_embed(
                        nick, date_str, reason,
                        "Система контроля нормы", total_warns,
                    )
                    try:
                        await warn_channel.send(
                            content=f"{member.mention} " if member else "",
                            embed=embed,
                        )
                    except discord.HTTPException:
                        log.exception("Failed to send norm warning")

                if warning_created and total_warns > 0:
                    await MinecraftPermissionsManager.send_private_minecraft_warning(
                        nick, reason, total_warns,
                        source="Персонал / контроль нормы",
                    )

                if warning_created and total_warns >= 3:
                    await auto_dismiss_staff(
                        self.bot, self.sheets_monitor,
                        self.staff_resolver, guild, nick,
                        reason="Автоматическое снятие: получено 3 предупреждения за нарушения суточной нормы онлайна",
                    )

        # Личные сообщения — только при надёжном ежедневном закрытии.
        if actions_allowed:
            for nick, position, hours_str, hours in exempt_list:
                member = (
                    self.staff_resolver.find_member_by_mc_nick(guild, nick)
                    if guild else None
                )
                if member:
                    await self.send_norm_dm(
                        member, nick, position, hours_str, hours,
                        norm_met=True, is_exempt=True,
                    )

            for nick, position, hours_str, hours in met_list:
                member = (
                    self.staff_resolver.find_member_by_mc_nick(guild, nick)
                    if guild else None
                )
                if member:
                    await self.send_norm_dm(
                        member, nick, position, hours_str, hours,
                        norm_met=True, is_exempt=False,
                    )

            for nick, position, hours_str, hours, warns in failed_list:
                member = (
                    self.staff_resolver.find_member_by_mc_nick(guild, nick)
                    if guild else None
                )
                if member:
                    await self.send_norm_dm(
                        member, nick, position, hours_str, hours,
                        norm_met=False, is_exempt=False,
                        total_warns=warns, warn_issued=warns > 0,
                    )

        # Отчёт
        preview = " (ПРЕДВАРИТЕЛЬНО)" if dry_run else ""
        embed = discord.Embed(
            title=f"📊 Проверка суточной нормы WeriTime — {date_str}{preview}",
            color=0x5865F2 if dry_run else (
                0x2ECC71 if not failed_list else 0xE67E22
            ),
            description=(
                f"**Норма:** {DAILY_NORM_HOURS:g} ч. за сутки.\n"
                f"**Всего сотрудников:** {len(staff_list)}\n"
                f"🟢 Выполнили: **{len(met_list)}**\n"
                f"🔴 Не выполнили: **{len(failed_list)}**\n"
                f"👑 Освобождены: **{len(exempt_list)}**\n"
                f"🏖️ В отпуске: **{len(vacation_list)}"
            ),
            timestamp=datetime.now(timezone.utc),
        )

        sections = [
            (
                "🟢 Выполнили норму",
                [f"• `{n}` — {p}: **{h}**"
                 for n, p, h, _ in met_list],
            ),
            (
                "🔴 Не выполнили норму",
                [f"• `{n}` — {p}: **{h}**"
                 + (f" • варнов: {w}" if w else "")
                 for n, p, h, _, w in failed_list],
            ),
            (
                "👑 Освобождены от нормы",
                [f"• `{n}` — {p}: **{h}**"
                 for n, p, h, _ in exempt_list],
            ),
            (
                "🏖️ В отпуске",
                [f"• `{n}` — {p}: **{h}**"
                 for n, p, h, _ in vacation_list],
            ),
                (
                    "🆕 Новички — норма ещё не действует",
                    [f"• `{n}` — {p}: **{h}**"
                     for n, p, h, _ in newcomer_list],
                ),
        ]

        embeds = [embed]
        for section_name, lines in sections:
            if not lines:
                continue
            chunks = []
            current = []
            length = 0
            for line in lines:
                if current and length + len(line) + 1 > 1000:
                    chunks.append("\n".join(current))
                    current, length = [], 0
                current.append(line)
                length += len(line) + 1
            if current:
                chunks.append("\n".join(current))

            for index, chunk in enumerate(chunks, 1):
                if len(embeds[-1].fields) >= 25:
                    embeds.append(
                        discord.Embed(
                            title=f"📊 Продолжение отчёта — {date_str}",
                            color=embed.color,
                        )
                    )
                suffix = (
                    f" ({index}/{len(chunks)})"
                    if len(chunks) > 1 else ""
                )
                embeds[-1].add_field(
                    name=section_name + suffix,
                    value=chunk,
                    inline=False,
                )

        for report in embeds:
            report.set_footer(
                text="WeriTime Staff System • Minecraft Query"
            )

        summary_sent = False
        summary_error = None

        if send_summary:
            if staff_channel is None:
                summary_error = "Не найден канал Персонал"
            else:
                try:
                    for report in embeds:
                        await staff_channel.send(embed=report)
                    summary_sent = True
                except discord.HTTPException as exc:
                    summary_error = str(exc)
                    log.exception("Failed to send daily report")

        return {
            "met": len(met_list),
            "failed": len(failed_list),
            "exempt": len(exempt_list),
            "vacation": len(vacation_list),
            "newcomers": len(newcomer_list),
            "total": len(staff_list),
            "reliable": reliable,
            "credited": should_credit,
            "summary_sent": summary_sent,
            "summary_error": summary_error,
        }
