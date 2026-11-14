"""
Periodic synchronization loop with Google Sheets:
- Real-time polling for new warnings and annulled warnings
- Detection of appointments, promotions and dismissals in personnel table
- Monthly reset of warnings on the 1st of each month
- Automated daily playtime norm check at 00:00 MSK
"""
from zoneinfo import ZoneInfo


import asyncio
from datetime import datetime, timezone, timedelta
import discord
from discord.ext import tasks
from src.core.config import (
    log, GUILD_ID, WARN_NOTIFY_CHANNEL, STAFF_NOTIFY_CHANNEL, POLL_INTERVAL
)
from src.utils.embeds import EmbedBuilder
from src.services.permissions import StaffRoleManager, MinecraftPermissionsManager
from src.services.sheets_api import STAFF_EMAILS


class SheetsSyncTask:
    """Фоновая служба периодической синхронизации с Google Sheets."""

    def __init__(self, bot, sheets_monitor, staff_resolver, playtime_tracker):
        self.bot = bot
        self.sheets_monitor = sheets_monitor
        self.staff_resolver = staff_resolver
        self.playtime_tracker = playtime_tracker
        self._loop = tasks.loop(seconds=POLL_INTERVAL)(self.poll_sheets)
        self._loop.before_loop(self.before_poll)
        self._loop.error(self.poll_sheets_error)
        self._midnight_task = None

    def start(self):
        """Запускает опрос Sheets и отдельный точный планировщик полуночи MSK."""
        if not self._loop.is_running():
            self._loop.start()
            log.info(f"Sheets poll loop started (interval: {POLL_INTERVAL}s)")
        if self._midnight_task is None or self._midnight_task.done():
            self._midnight_task = asyncio.create_task(self._run_midnight_schedule())

    def stop(self):
        """Останавливает фоновые задачи."""
        if self._loop.is_running():
            self._loop.stop()
        if self._midnight_task and not self._midnight_task.done():
            self._midnight_task.cancel()

    async def _run_midnight_schedule(self):
        await self.bot.wait_until_ready()
        tz = ZoneInfo("Europe/Moscow")
        while True:
            now = datetime.now(tz)
            target = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                      + timedelta(days=1))
            # If the bot starts during the midnight minute and today's check is pending,
            # run immediately instead of waiting a full day.
            today_key = now.strftime("%Y-%m-%d")
            if now.hour == 0 and now.minute < 5 and self.sheets_monitor and (
                getattr(self.sheets_monitor, "last_playtime_date", "") != today_key
            ):
                target = now
            await asyncio.sleep(max(0.0, (target - datetime.now(tz)).total_seconds()))
            run_date = datetime.now(tz).strftime("%Y-%m-%d")
            if not self.sheets_monitor or getattr(self.sheets_monitor, "last_playtime_date", "") == run_date:
                continue
            guild = self.bot.get_guild(GUILD_ID)
            if not guild:
                log.error("Midnight norm check skipped: configured Discord guild is unavailable")
                continue
            # Retry temporary Pterodactyl / Sheets failures without marking the day complete.
            for attempt in range(10):
                try:
                    result = await self.playtime_tracker.process_daily_playtime_and_norms(
                        guild, dry_run=False, is_midnight=True
                    )
                    if result and not result.get("error"):
                        self.sheets_monitor.last_playtime_date = run_date
                        self.sheets_monitor._save_state()
                        log.info("Daily norm check completed for %s at Europe/Moscow midnight", run_date)
                        break
                    log.warning("Midnight norm check attempt %s failed: %s", attempt + 1,
                                (result or {}).get("error", "empty result"))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Midnight norm check attempt %s failed", attempt + 1)
                await asyncio.sleep(30)

    async def before_poll(self):
        await self.bot.wait_until_ready()

    async def poll_sheets_error(self, error):
        log.error(f"poll_sheets loop error: {error}", exc_info=error)
        await asyncio.sleep(10)
        if not self._loop.is_running():
            try:
                self._loop.restart()
                log.info("poll_sheets loop restarted successfully")
            except Exception as e:
                log.error(f"Failed to restart poll_sheets loop: {e}")

    async def poll_sheets(self):
        """Регулярная проверка изменений в Google Таблицах."""
        if not self.sheets_monitor:
            return

        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            return

        warn_channel = guild.get_channel(WARN_NOTIFY_CHANNEL)
        staff_channel = guild.get_channel(STAFF_NOTIFY_CHANNEL)

        # Normalize Discord column to actual usernames (member.name), not numeric IDs.
        try:
            for staff_entry in self.sheets_monitor.get_staff_list():
                nick = (staff_entry.get("nick") or "").strip()
                discord_value = (staff_entry.get("discord") or "").strip().lstrip("@")
                if not nick:
                    continue
                member = None
                if discord_value.isdigit():
                    member = guild.get_member(int(discord_value))
                    if member is None:
                        try:
                            member = await guild.fetch_member(int(discord_value))
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            member = None
                if member is None:
                    member = self.staff_resolver.find_member_by_mc_nick(guild, nick)
                if member and discord_value != member.name:
                    self.sheets_monitor.update_staff_discord_username(nick, member.name)
        except Exception as e:
            log.warning(f"Could not normalize Discord usernames in staff sheet: {e}")

        # Refresh the in-memory lookup too, so midnight DM delivery uses the new usernames.
        try:
            self.staff_resolver.populate_sheets_discord_map()
        except Exception as e:
            log.warning(f"Could not refresh Discord username cache: {e}")

        now = datetime.now(ZoneInfo("Europe/Moscow"))
        current_month = now.strftime("%Y-%m")

        # 1. Ежемесячный сброс предупреждений 1-го числа
        if self.sheets_monitor.last_reset_month and self.sheets_monitor.last_reset_month != current_month:
            log.info(f"New month detected ({current_month} != {self.sheets_monitor.last_reset_month}). Triggering monthly reset...")
            success = self.sheets_monitor.reset_monthly_warnings()
            if success:
                warn_role = discord.utils.get(guild.roles, name="⚠️ На испытании")
                if warn_role:
                    for member in warn_role.members:
                        try:
                            await member.remove_roles(warn_role, reason="Ежемесячный сброс предупреждений")
                            log.info(f"Removed probation role from {member} during monthly reset")
                        except Exception as e:
                            log.error(f"Failed to remove role from {member}: {e}")

                if warn_channel:
                    month_names = {
                        "01": "Январь", "02": "Февраль", "03": "Март", "04": "Апрель",
                        "05": "Май", "06": "Июнь", "07": "Июль", "08": "Август",
                        "09": "Сентябрь", "10": "Октябрь", "11": "Ноябрь", "12": "Декабрь"
                    }
                    m_str = month_names.get(now.strftime("%m"), current_month)
                    embed = EmbedBuilder.build_monthly_reset_embed(f"{m_str} {now.year}")
                    await warn_channel.send(embed=embed)

        # 2. Обработка добавления и снятия предупреждений
        try:
            warn_data = self.sheets_monitor.check_warnings()
        except Exception as e:
            log.error(f"Error checking warnings: {e}")
            warn_data = {"new": [], "removed_by_player": {}, "current_counts": {}}

        for warn in warn_data["new"]:
            nick = warn["nick"]
            date = warn["date"]
            reason = warn["reason"]
            issued_by = warn["issued_by"]
            total_warns = warn["total_warns"]

            log.info(f"⚠️ New warning: {nick} — {reason} (by {issued_by}, total: {total_warns})")
            member = self.staff_resolver.find_member_by_mc_nick(guild, nick)
            ping_text = f"{member.mention} " if member else ""

            embed = EmbedBuilder.build_warn_embed(nick, date, reason, issued_by, total_warns)
            if warn_channel:
                await warn_channel.send(content=ping_text, embed=embed)

            if total_warns >= 3 and member:
                warn_role = discord.utils.get(guild.roles, name="⚠️ На испытании")
                if not warn_role:
                    try:
                        warn_role = await guild.create_role(
                            name="⚠️ На испытании",
                            color=discord.Color.red(),
                            reason="Auto-created by WeriGrief Staff System",
                        )
                    except Exception as e:
                        log.error(f"Failed to create role: {e}")

                if warn_role and warn_role not in member.roles:
                    try:
                        await member.add_roles(warn_role, reason=f"Набрал {total_warns} предупреждений")
                        log.info(f"Assigned '⚠️ На испытании' to {member}")
                    except Exception as e:
                        log.error(f"Failed to assign role to {member}: {e}")

        removed_by_player = warn_data.get("removed_by_player", {})
        for p_nick, p_info in removed_by_player.items():
            rem_count = p_info["removed_count"]
            curr_count = p_info["current_count"]
            rem_items = p_info["removed_items"]

            log.info(f"✅ Warnings cleared for {p_nick}: removed {rem_count}, current: {curr_count}")
            member = self.staff_resolver.find_member_by_mc_nick(guild, p_nick)
            ping_text = f"{member.mention} " if member else ""

            role_removed = False
            if member and curr_count < 3:
                warn_role = discord.utils.get(guild.roles, name="⚠️ На испытании")
                if warn_role and warn_role in member.roles:
                    try:
                        await member.remove_roles(warn_role, reason="Предупреждения сняты администратором (менее 3)")
                        role_removed = True
                        log.info(f"Removed '⚠️ На испытании' from {member}")
                    except Exception as e:
                        log.error(f"Failed to remove probation role: {e}")

            embed = EmbedBuilder.build_player_warns_cleared_embed(p_nick, rem_count, curr_count, rem_items, role_removed)
            if warn_channel:
                await warn_channel.send(content=ping_text, embed=embed)

        # 3. Изменения в составе персонала (назначения, переводы, увольнения)
        try:
            staff_changes = self.sheets_monitor.check_staff_changes()
        except Exception as e:
            log.error(f"Error checking staff changes: {e}")
            staff_changes = {"appointed": [], "promoted": [], "removed": []}

        for ap in staff_changes["appointed"]:
            nick = ap["nick"]
            pos = ap.get("position", "Сотрудник")
            disc = ap.get("discord", "")
            email = ap.get("email", STAFF_EMAILS.get(nick.lower(), ""))
            comment = ap.get("comment", "")
            appointed_by = ap.get("appointed_by", "Руководство проекта")

            log.info(f"🛡️ Staff appointed: {nick} ({pos}) [Discord: {disc}] [By: {appointed_by}]")
            self.staff_resolver.populate_sheets_discord_map()
            member = self.staff_resolver.find_member_by_mc_nick(guild, nick)
            ping_text = f"{member.mention} " if member else ""

            roles_granted, roles_blocked = [], []
            if member:
                roles_granted, roles_blocked = await StaffRoleManager.grant_discord_staff_roles(
                    member, position=pos, reason=f"Назначение в персонал ({pos})"
                )

            mc_granted, mc_group = await MinecraftPermissionsManager.grant_minecraft_permissions(nick, position=pos)

            embed = EmbedBuilder.build_staff_appointed_embed(
                nick=nick,
                position=pos,
                discord_name=disc,
                appointed_by=appointed_by,
                email=email,
                comment=comment,
                roles_granted=roles_granted,
                roles_blocked=roles_blocked,
                mc_group=mc_group,
                mc_granted=mc_granted,
            )
            if staff_channel:
                await staff_channel.send(content=ping_text, embed=embed)

        for pr in staff_changes.get("promoted", []):
            nick = pr["nick"]
            old_pos = pr["old_position"]
            new_pos = pr["new_position"]
            disc = pr.get("discord", "")
            email = pr.get("email", STAFF_EMAILS.get(nick.lower(), ""))
            promoted_by = pr.get("promoted_by", "Руководство проекта")

            log.info(f"📈 Staff position changed: {nick} ({old_pos} ➔ {new_pos}) [By: {promoted_by}]")
            member = self.staff_resolver.find_member_by_mc_nick(guild, nick)
            ping_text = f"{member.mention} " if member else ""

            roles_granted, roles_blocked = [], []
            if member:
                roles_granted, roles_blocked = await StaffRoleManager.grant_discord_staff_roles(
                    member, position=new_pos, old_position=old_pos, reason=f"Повышение: {old_pos} -> {new_pos}"
                )

            mc_granted, mc_group = await MinecraftPermissionsManager.grant_minecraft_permissions(nick, position=new_pos)

            embed = EmbedBuilder.build_staff_promoted_embed(
                nick=nick,
                old_pos=old_pos,
                new_pos=new_pos,
                promoted_by=promoted_by,
                discord_name=disc,
                email=email,
                roles_granted=roles_granted,
                roles_blocked=roles_blocked,
                mc_group=mc_group,
                mc_granted=mc_granted,
            )
            if staff_channel:
                await staff_channel.send(content=ping_text, embed=embed)

        for rm in staff_changes["removed"]:
            nick = rm["nick"]
            pos = rm.get("position", "Сотрудник")
            email = rm.get("email", STAFF_EMAILS.get(nick.lower(), ""))
            removed_by = rm.get("removed_by", "Руководство проекта")
            reason = rm.get("reason", "")
            warns_cleared = rm.get("warns_cleared", 0)

            log.info(f"👋 Staff removed: {nick} ({pos}) [By: {removed_by}]")
            member = self.staff_resolver.find_member_by_mc_nick(guild, nick)
            ping_text = f"{member.mention} " if member else ""

            if member:
                await StaffRoleManager.strip_discord_staff_roles(
                    member, reason=f"Сотрудник снят с должности персонала ({pos})"
                )
                await EmbedBuilder.send_dismissal_dm(
                    member, nick=nick, position=pos, removed_by=removed_by, reason=reason
                )

            await MinecraftPermissionsManager.revoke_minecraft_permissions(nick)

            embed = EmbedBuilder.build_staff_removed_embed(
                nick=nick,
                position=pos,
                email=email,
                warns_cleared=warns_cleared,
                removed_by=removed_by,
                reason=reason,
            )
            if staff_channel:
                await staff_channel.send(content=ping_text, embed=embed)

