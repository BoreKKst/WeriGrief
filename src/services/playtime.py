"""
Playtime calculation, Minecraft stats fetching via Pterodactyl API, and daily norm tracking.
"""
from zoneinfo import ZoneInfo


import asyncio
import hashlib
import re
import uuid
from datetime import datetime, timezone, timedelta
import aiohttp
import discord
from src.core.config import (
    log, PTERO_API_KEY, PTERO_BASE_URL, GAME_SERVERS, DAILY_NORM_HOURS,
    WARN_NOTIFY_CHANNEL, STAFF_NOTIFY_CHANNEL, MC_STATS_DIRECTORY
)
from src.utils.embeds import EmbedBuilder
from src.services.permissions import MinecraftPermissionsManager


class PlaytimeTracker:
    """Класс для отслеживания онлайна персонала и суточной нормы активности."""

    def __init__(self, sheets_monitor, staff_resolver):
        self.sheets_monitor = sheets_monitor
        self.staff_resolver = staff_resolver

    @staticmethod
    def mc_offline_uuid(name: str) -> str:
        """Генерирует оффлайн UUID игрока Minecraft по никнейму."""
        b = bytearray(hashlib.md5(('OfflinePlayer:' + name).encode('utf-8')).digest())
        b[6] = (b[6] & 0x0f) | 0x30
        b[8] = (b[8] & 0x3f) | 0x80
        return str(uuid.UUID(bytes=bytes(b)))

    @staticmethod
    def format_ticks_to_hours(ticks: int) -> tuple[float, str]:
        """Преобразует тики игрового времени (20 тиков = 1 сек) в часы и строку."""
        total_sec = ticks // 20
        hours_float = ticks / 72000.0
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        if h > 0:
            return hours_float, f"{h}ч {m}м" if m > 0 else f"{h}ч"
        return hours_float, f"{m}м"

    @staticmethod
    def format_hours_delta(h: float) -> str:
        """Форматирует часы float в читаемый вид 'Xч Yм'."""
        total_mins = int(round(h * 60))
        hrs = total_mins // 60
        mins = total_mins % 60
        if hrs > 0 and mins > 0:
            return f"{hrs}ч {mins}м"
        elif hrs > 0:
            return f"{hrs}ч"
        else:
            return f"{mins}м"

    def is_norm_exempt(self, position: str, mc_nick: str = "") -> bool:
        """
        Проверяет, освобожден ли сотрудник от суточной нормы (руководство или индивидуальное освобождение).
        """
        pos_low = position.lower()
        exempt_keywords = ["владелец", "со-владелец", "совладелец", "co-owner", "owner", "куратор", "curator", "руковод", "создатель"]
        if any(k in pos_low for k in exempt_keywords):
            return True
        nick_low = mc_nick.strip().lower()
        nick_no_us = nick_low.replace("_", "")
        if self.sheets_monitor and (self.sheets_monitor.is_custom_exempt(nick_low) or self.sheets_monitor.is_custom_exempt(nick_no_us)):
            return True
        return False

    @staticmethod
    def calculate_today_norm_delta(current_hours: float, snapshot: dict | None) -> tuple[float, bool]:
        """
        Рассчитывает дельту онлайна за текущие сутки (разница между текущим онлайном и слепком в 00:00).
        Возвращает: (today_delta_hours, is_baseline)
        """
        if not snapshot:
            return 0.0, True

        prev_hours = snapshot.get("hours")
        if prev_hours is None:
            return 0.0, True

        if current_hours >= prev_hours:
            return (current_hours - prev_hours), False

        diff = prev_hours - current_hours
        if diff <= 0.15:
            return 0.0, False
        else:
            return current_hours, True

    async def fetch_playtime_for_staff(self, nicks: list[str], flush_save: bool = False) -> dict[str, dict]:
        """
        Запрашивает актуальный онлайн сотрудников со всех игровых серверов через чтение stats/uuid.json в Pterodactyl API.
        """
        if not PTERO_API_KEY:
            log.warning("PTERO_API_KEY not configured, cannot fetch playtime")
            return {}

        headers = {
            "Authorization": f"Bearer {PTERO_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        results = {}
        sem = asyncio.Semaphore(4)
        async with aiohttp.ClientSession() as session:
            if flush_save:
                save_tasks = []
                for srv in GAME_SERVERS:
                    cmd_url = f"{PTERO_BASE_URL}/api/client/servers/{srv}/command"
                    save_tasks.append(session.post(cmd_url, json={"command": "save-all"}, headers=headers, timeout=3))
                try:
                    await asyncio.gather(*save_tasks, return_exceptions=True)
                except Exception as e:
                    log.debug(f"save-all batch error: {e}")

            async def _get_server_ticks(srv: str, u: str) -> int:
                from urllib.parse import quote
                stats_file = f"{MC_STATS_DIRECTORY}/{u}.json"
                url = f"{PTERO_BASE_URL}/api/client/servers/{srv}/files/contents?file={quote(stats_file, safe='')}"
                async with sem:
                    for attempt in range(2):
                        try:
                            async with session.get(url, headers=headers, timeout=6) as resp:
                                if resp.status == 200:
                                    data = await resp.json(content_type=None)
                                    c = data.get("stats", {}).get("minecraft:custom", {})
                                    return c.get("minecraft:play_time") or c.get("minecraft:play_one_minute") or 0
                                elif resp.status == 404:
                                    log.debug(
                                        "Stats file not found for UUID %s on server %s at %s; check MC_STATS_DIRECTORY and UUID mode",
                                        u, srv, stats_file
                                    )
                                    return None
                                else:
                                    log.warning(
                                        "Pterodactyl stats request failed: server=%s status=%s nick_uuid=%s",
                                        srv, resp.status, u
                                    )
                                    return None
                        except Exception as e:
                            if attempt == 0:
                                await asyncio.sleep(0.15)
                            else:
                                log.debug(f"Error reading stats for {u} on {srv}: {e}")
                return None

            clean_nicks = [n.strip() for n in nicks if n.strip()]
            for clean in clean_nicks:
                u = self.mc_offline_uuid(clean)
                tasks = [_get_server_ticks(srv, u) for srv in GAME_SERVERS]
                tick_results = await asyncio.gather(*tasks, return_exceptions=True)
                valid_ticks = [t for t in tick_results if isinstance(t, (int, float))]
                if not valid_ticks:
                    log.error("No readable stats files for Minecraft player %s on configured servers", clean)
                    continue
                total_ticks = int(sum(valid_ticks))
                h_float, h_str = self.format_ticks_to_hours(total_ticks)
                results[clean.lower()] = {
                    "nick": clean,
                    "raw_time": h_str,
                    "hours": h_float,
                    "hours_str": h_str,
                    "hours_table": str(int(round(h_float))),
                }

        return results

    async def get_staff_norm_info(self, mc_nick: str) -> dict | None:
        """
        Возвращает данные о прогрессе суточной нормы для команды /norm.
        """
        if not self.sheets_monitor:
            return None

        staff_list = self.sheets_monitor.get_staff_list()
        matched_staff = None
        target_low = mc_nick.strip().lower()

        for s in staff_list:
            if s.get("nick") and s["nick"].lower() == target_low:
                matched_staff = s
                break
            d = s.get("discord", "").strip().lower()
            if d and (d == target_low or d.replace("_", "") == target_low.replace("_", "")):
                matched_staff = s
                break

        if not matched_staff:
            matched_staff = {"nick": mc_nick, "position": "Сотрудник", "hours": "0", "warns": "0"}

        actual_nick = matched_staff["nick"]
        pos = matched_staff.get("position", "Сотрудник")
        sheet_hours = matched_staff.get("hours", "0")
        warn_count = matched_staff.get("warns", "0")
        exempt = self.is_norm_exempt(pos, actual_nick)

        playtime_data = await self.fetch_playtime_for_staff([actual_nick], flush_save=False)
        p_info = playtime_data.get(actual_nick.lower(), {})
        current_mc_hours = p_info.get("hours", 0.0)
        lifetime_str = p_info.get("hours_str", "0м")

        snapshot = self.sheets_monitor.mc_playtime_snapshots.get(actual_nick.lower()) if self.sheets_monitor else None
        if not snapshot and current_mc_hours > 0.0 and self.sheets_monitor:
            today_k = (datetime.now(ZoneInfo("Europe/Moscow"))).strftime("%Y-%m-%d")
            recovered_base = 0.0
            try:
                sh_val = float(str(sheet_hours).replace(",", "."))
                if sh_val > 0.0:
                    recovered_base = max(0.0, current_mc_hours - sh_val)
            except Exception:
                pass
            snapshot = {
                "hours": recovered_base if recovered_base > 0.0 else current_mc_hours,
                "date": today_k,
                "timestamp": datetime.now(timezone.utc).timestamp(),
            }
            self.sheets_monitor.mc_playtime_snapshots[actual_nick.lower()] = snapshot
            self.sheets_monitor._save_state()

        today_delta, is_baseline = self.calculate_today_norm_delta(current_mc_hours, snapshot)
        today_str = self.format_hours_delta(today_delta)
        norm_met = today_delta >= DAILY_NORM_HOURS
        remaining_hours = max(0.0, DAILY_NORM_HOURS - today_delta)
        remaining_str = self.format_hours_delta(remaining_hours)

        percent = min(100, int((today_delta / DAILY_NORM_HOURS) * 100))
        filled_blocks = min(10, int(percent / 10))
        empty_blocks = 10 - filled_blocks
        progress_bar = f"[{'█' * filled_blocks}{'░' * empty_blocks}]"

        return {
            "nick": actual_nick,
            "position": pos,
            "sheet_hours": sheet_hours,
            "warn_count": warn_count,
            "is_exempt": exempt,
            "lifetime_hours": current_mc_hours,
            "lifetime_str": lifetime_str,
            "today_delta": today_delta,
            "today_str": today_str,
            "norm_met": norm_met,
            "remaining_hours": remaining_hours,
            "remaining_str": remaining_str,
            "percent": percent,
            "progress_bar": progress_bar,
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
                    f"Спасибо за активную работу и вклад в развитие проекта **✨WeriGrief✨**! 🚀"
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
                    f"Пожалуйста, соблюдайте нормы активности на проекте **✨WeriGrief✨**."
                ),
                timestamp=datetime.now(timezone.utc),
            )
        embed.set_footer(text="WeriGrief Staff System • Контроль онлайна")

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
        self, guild: discord.Guild, dry_run: bool = False, is_midnight: bool = False, send_summary: bool = True
    ) -> dict:
        """
        Ежедневная проверка онлайна и нормы в 00:00 МСК:
        1. Снимает статистику /playtime со всех игровых серверов с предварительным flush save-all.
        2. Сравнивает со слепком предыдущего дня.
        3. Начисляет отыгранные часы в Google Таблицу ('Списки', колонка E; схема A:F).
        4. Сотрудникам без освобождения, наигравшим < 1ч, выдает варн в таблице.
        5. Рассылает личные сообщения и итоговый отчёт в канал персонала.
        """
        if not self.sheets_monitor:
            log.error("Sheets monitor not ready for daily norm check")
            return {}

        staff_list = [s for s in self.sheets_monitor.get_staff_list() if s.get("nick")]
        if not staff_list:
            log.warning("No staff found in sheets for daily norm check")
            return {}

        nicks = [s["nick"] for s in staff_list]
        log.info(f"Running daily playtime check for {len(nicks)} staff members (dry_run={dry_run}, is_midnight={is_midnight})...")

        playtime_data = await self.fetch_playtime_for_staff(
            nicks, flush_save=(not dry_run and is_midnight)
        )
        if not playtime_data:
            log.error("Daily norm check aborted: no readable Minecraft stats were returned.")
            return {"error": "Не удалось получить статистику Minecraft из Pterodactyl. Проверь PTERO_API_KEY, ID сервера, MC_STATS_DIRECTORY и UUID игроков. Варны и часы не изменены."}

        msk_time = datetime.now(ZoneInfo("Europe/Moscow"))
        date_str = msk_time.strftime("%d.%m.%Y")
        today_key = msk_time.strftime("%Y-%m-%d")

        warn_channel = guild.get_channel(WARN_NOTIFY_CHANNEL) if guild and WARN_NOTIFY_CHANNEL else None
        staff_channel = guild.get_channel(STAFF_NOTIFY_CHANNEL) if guild and STAFF_NOTIFY_CHANNEL else None
        # get_channel() зависит от локального Discord-кэша. Для отчёта обязательно
        # пробуем REST-fetch, иначе команда могла сообщить об успехе без отправки.
        if send_summary and guild and staff_channel is None and STAFF_NOTIFY_CHANNEL:
            try:
                staff_channel = await guild.fetch_channel(STAFF_NOTIFY_CHANNEL)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                log.error("Cannot fetch staff report channel %s: %s", STAFF_NOTIFY_CHANNEL, exc)

        met_list = []
        failed_list = []
        exempt_list = []
        baseline_first_day_list = []
        unavailable_list = []
        hours_delta_map = {}

        for s in staff_list:
            nick = s["nick"]
            pos = s.get("position", "Сотрудник")
            exempt = self.is_norm_exempt(pos, nick)

            # Отпуск освобождает сотрудника от нормы на даты отпуска.
            if self.sheets_monitor.is_on_vacation(nick):
                exempt_list.append((nick, f"{pos} • отпуск", "Отпуск", 0.0))
                continue

            p_info = playtime_data.get(nick.lower())
            if p_info is None:
                unavailable_list.append(nick)
                continue
            current_mc_hours = p_info.get("hours", 0.0)

            snapshot = self.sheets_monitor.mc_playtime_snapshots.get(nick.lower()) if self.sheets_monitor else None
            delta_hours, is_baseline_day = self.calculate_today_norm_delta(current_mc_hours, snapshot)

            hours_delta_map[nick.lower()] = delta_hours
            delta_str = self.format_hours_delta(delta_hours)
            member = self.staff_resolver.find_member_by_mc_nick(guild, nick) if guild else None

            if exempt:
                exempt_list.append((nick, pos, delta_str, delta_hours))
                if member and not dry_run and is_midnight:
                    await self.send_norm_dm(member, nick, pos, delta_str, delta_hours, norm_met=True, is_exempt=True)
                continue

            # Защита новичков: в первый неполный день варн не выдается, но если <1ч — не отмечаем как выполнивших
            if is_baseline_day:
                if delta_hours >= DAILY_NORM_HOURS:
                    met_list.append((nick, pos, delta_str, delta_hours))
                    if member and not dry_run and is_midnight:
                        await self.send_norm_dm(member, nick, pos, delta_str, delta_hours, norm_met=True, is_exempt=False)
                else:
                    baseline_first_day_list.append((nick, pos, delta_str, delta_hours))
                    if member and not dry_run and is_midnight:
                        await self.send_norm_dm(member, nick, pos, delta_str, delta_hours, norm_met=True, is_exempt=True)
                continue

            norm_met = delta_hours >= DAILY_NORM_HOURS
            if norm_met:
                met_list.append((nick, pos, delta_str, delta_hours))
                if member and not dry_run and is_midnight:
                    await self.send_norm_dm(member, nick, pos, delta_str, delta_hours, norm_met=True, is_exempt=False)
            else:
                total_warns = 0
                warn_issued = False
                if not dry_run and is_midnight:
                    reason = f"Невыполнение суточной нормы онлайна (наиграно: {delta_str} из 1ч)"
                    total_warns = self.sheets_monitor.add_warning(nick, date_str, reason, "Система контроля нормы")
                    warn_issued = total_warns > 0

                    if total_warns >= 3 and member:
                        warn_role = discord.utils.get(guild.roles, name="⚠️ На испытании")
                        if warn_role and warn_role not in member.roles:
                            try:
                                await member.add_roles(warn_role, reason="Авто-предупреждение: невыполнение нормы онлайна (3+ варна)")
                            except Exception as e:
                                log.error(f"Failed to add probation role to {member}: {e}")

                    if warn_channel and warn_issued:
                        ping = f"{member.mention} " if member else ""
                        embed = EmbedBuilder.build_warn_embed(nick, date_str, reason, "Система контроля нормы", total_warns)
                        await warn_channel.send(content=ping, embed=embed)
                    if warn_issued:
                        await MinecraftPermissionsManager.send_private_minecraft_warning(
                            nick, reason, total_warns, source="Персонал / контроль нормы"
                        )

                failed_list.append((nick, pos, delta_str, delta_hours, total_warns))
                if member and not dry_run and is_midnight:
                    await self.send_norm_dm(
                        member, nick, pos, delta_str, delta_hours,
                        norm_met=False, is_exempt=False,
                        total_warns=total_warns, warn_issued=warn_issued
                    )

        if not dry_run and is_midnight:
            self.sheets_monitor.increment_staff_hours(hours_delta_map)
            for nick in nicks:
                p_info = playtime_data.get(nick.lower())
                if p_info is None:
                    continue
                curr_h = p_info.get("hours", 0.0)
                self.sheets_monitor.mc_playtime_snapshots[nick.lower()] = {
                    "hours": curr_h,
                    "date": today_key,
                    "timestamp": datetime.now(timezone.utc).timestamp(),
                }
            self.sheets_monitor._save_state()

        preview_label = " (ПРЕДВАРИТЕЛЬНО • без предупреждений и изменений)" if dry_run else ""
        summary_embed = discord.Embed(
            title=f"📊 Проверка суточной нормы WeriGrief — {date_str}{preview_label}",
            color=0x5865F2 if dry_run else (0x2ECC71 if not failed_list else 0xE67E22),
            description=(
                f"**Норма:** {DAILY_NORM_HOURS:g} ч. чистого онлайна за сутки.\n"
                f"**Всего в составе:** {len(staff_list)}\n\n"
                f"🟢 Выполнили: **{len(met_list)}**\n"
                f"🔴 Не выполнили: **{len(failed_list)}**\n"
                f"👑 Освобождены / отпуск: **{len(exempt_list)}**\n"
                f"🆕 Первый день / нет baseline: **{len(baseline_first_day_list)}**\n"
                f"⚪ Нет данных Pterodactyl: **{len(unavailable_list)}**"
            ),
            timestamp=datetime.now(timezone.utc),
        )

        # Все категории выводятся полностью. Делим длинные списки на поля,
        # чтобы не обрезать состав ограничением Discord в 1024 символа на поле.
        sections = [
            ("🟢 Выполнили норму", [f"• `{n}` — {p}: **{h}**" for n, p, h, _ in met_list]),
            ("🔴 Не выполнили норму" + (" • без варна" if dry_run else ""), [f"• `{n}` — {p}: **{h}**" + (f" • варнов: {w}" if w else "") for n, p, h, _, w in failed_list]),
            ("👑 Освобождены / отпуск", [f"• `{n}` — {p}: **{h}**" for n, p, h, _ in exempt_list]),
            ("🆕 Первый день / baseline", [f"• `{n}` — {p}: **{h}**" for n, p, h, _ in baseline_first_day_list]),
            ("⚪ Нет данных Pterodactyl", [f"• `{n}` — статистика недоступна" for n in unavailable_list]),
        ]
        embeds = [summary_embed]
        for section_name, lines in sections:
            if not lines:
                continue
            chunks, current = [], []
            current_len = 0
            for line in lines:
                if current and current_len + len(line) + 1 > 1000:
                    chunks.append("\n".join(current))
                    current, current_len = [], 0
                current.append(line)
                current_len += len(line) + 1
            if current:
                chunks.append("\n".join(current))
            for idx, chunk in enumerate(chunks, start=1):
                suffix = f" ({idx}/{len(chunks)})" if len(chunks) > 1 else ""
                if len(summary_embed.fields) >= 25:
                    extra = discord.Embed(title=f"📊 Продолжение отчёта — {date_str}", color=summary_embed.color)
                    embeds.append(extra)
                embeds[-1].add_field(name=section_name + suffix, value=chunk, inline=False)

        for e in embeds:
            e.set_footer(text="WeriGrief Staff System • Автоматическая проверка ежедневно в 00:00 Europe/Moscow")

        summary_sent = False
        summary_error = None
        if send_summary:
            if staff_channel is None:
                summary_error = f"Не найден канал Персонал (ID {STAFF_NOTIFY_CHANNEL}). Проверьте ID и права бота View Channel / Send Messages / Embed Links."
                log.error(summary_error)
            else:
                try:
                    for e in embeds:
                        await staff_channel.send(embed=e)
                    summary_sent = True
                    log.info("Full norm report sent to staff channel %s (%s embeds, %s staff)", STAFF_NOTIFY_CHANNEL, len(embeds), len(staff_list))
                except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                    summary_error = f"Discord не принял отчёт в канал Персонал: {type(exc).__name__}: {exc}"
                    log.exception(summary_error)

        return {
            "met": len(met_list),
            "failed": len(failed_list),
            "exempt": len(exempt_list),
            "baseline": len(baseline_first_day_list),
            "unavailable": len(unavailable_list),
            "total": len(staff_list),
            "summary_sent": summary_sent,
            "summary_error": summary_error,
        }

