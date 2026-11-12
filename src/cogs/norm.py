"""
Cog for playtime and norm monitoring commands (/norm, /check_norm, /norm_exempt, /help_staff).
"""

from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands
from src.core.config import GUILD_ID, DAILY_NORM_HOURS, LEADERSHIP_IDS


class NormCog(commands.Cog, name="Norm"):
    """Команды контроля суточной нормы онлайна персонала."""

    def __init__(self, bot, sheets_monitor, staff_resolver, playtime_tracker):
        self.bot = bot
        self.sheets_monitor = sheets_monitor
        self.staff_resolver = staff_resolver
        self.playtime_tracker = playtime_tracker

    @commands.hybrid_command(
        name="check_norm",
        aliases=["checknorm", "syncplaytime"],
        description="Проверить суточную норму онлайна (1ч) и обновить часы в таблице (для Администрации)",
    )
    @app_commands.describe(
        dry_run="Только просмотр без выдачи предупреждений (по умолчанию True при ручном вызове)"
    )
    async def cmd_check_norm(self, ctx: commands.Context, dry_run: bool = True):
        """Ручной запуск проверки суточной нормы онлайна."""
        await ctx.defer()
        guild = ctx.guild or self.bot.get_guild(GUILD_ID)
        if not self.staff_resolver.is_admin_or_higher(ctx.author, guild):
            await ctx.send("❌ Команда доступна только **Администраторам**, **Старшим администраторам** и руководству проекта.", ephemeral=True)
            return

        if not dry_run:
            await ctx.send("ℹ️ Примечание: начисление варнов и фиксация новых суточных счетчиков онлайна производится автоматически исключительно в 00:00 MSK. Проверка выполнена в режиме предпросмотра.")
            dry_run = True

        res = await self.playtime_tracker.process_daily_playtime_and_norms(guild, dry_run=dry_run, is_midnight=False)
        mode_str = "Тестовый режим (без варнов)"
        await ctx.send(f"✅ Проверка онлайна завершена [{mode_str}]! Выполнили норму: **{res.get('met', 0)}**, Не выполнили: **{res.get('failed', 0)}**, Руководство: **{res.get('exempt', 0)}**.")

    @commands.hybrid_command(
        name="norm",
        aliases=["norma", "playtime_norm"],
        description="Просмотр суточной нормы онлайна сотрудника (чистый онлайн за сегодня с 00:00 MSK)",
    )
    @app_commands.describe(target="Никнейм в игре или @Discord сотрудника (если пусто — своя норма или список всех)")
    async def cmd_norm(self, ctx: commands.Context, *, target: str = None):
        """Отображает текущий прогресс суточной нормы онлайна сотрудника или всех сотрудников."""
        await ctx.defer()

        guild = ctx.guild or self.bot.get_guild(GUILD_ID)
        if not guild:
            await ctx.send("❌ Сервер не найден.")
            return

        author_staff = self.staff_resolver.get_staff_entry_for_member(ctx.author)
        if not author_staff and ctx.author.id not in LEADERSHIP_IDS:
            await ctx.send(
                "❌ **Доступ запрещен:** вы не числитесь в официальной таблице персонала (Google Sheets).\n"
                "Наличие роли или прав в Discord без записи в таблице не дает права на использование команд персонала.",
                ephemeral=True,
            )
            return

        admin_caller = self.staff_resolver.is_admin_or_higher(ctx.author, guild)

        if not target:
            if admin_caller:
                staff_list = [s for s in self.sheets_monitor.get_staff_list() if s.get("nick")] if self.sheets_monitor else []
                if not staff_list:
                    await ctx.send("ℹ️ Список персонала пуст.")
                    return

                nicks = [s["nick"] for s in staff_list]
                playtime_data = await self.playtime_tracker.fetch_playtime_for_staff(nicks)

                embed = discord.Embed(
                    title="⏱ Суточная норма онлайна персонала (За сегодня)",
                    color=0x5865F2,
                    description=(
                        "**Регламент:** 1 час в сутки (чистый онлайн за день с 00:00 MSK).\n"
                        "Для подробной карточки сотрудника используйте `/norm <ник>`."
                    ),
                    timestamp=datetime.now(timezone.utc),
                )

                lines = []
                for s in staff_list:
                    nick = s["nick"]
                    pos = s.get("position", "Сотрудник")
                    exempt = self.playtime_tracker.is_norm_exempt(pos, nick)

                    p_info = playtime_data.get(nick.lower(), {})
                    curr_h = p_info.get("hours", 0.0)

                    snapshot = self.sheets_monitor.mc_playtime_snapshots.get(nick.lower()) if self.sheets_monitor else None
                    if not snapshot and curr_h > 0.0 and self.sheets_monitor:
                        today_k = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d")
                        snapshot = {
                            "hours": curr_h,
                            "date": today_k,
                            "timestamp": datetime.now(timezone.utc).timestamp(),
                        }
                        self.sheets_monitor.mc_playtime_snapshots[nick.lower()] = snapshot
                        self.sheets_monitor._save_state()

                    delta_h, is_baseline = self.playtime_tracker.calculate_today_norm_delta(curr_h, snapshot)
                    delta_str = self.playtime_tracker.format_hours_delta(delta_h)

                    if exempt:
                        status = f"👑 `{nick}` ({pos}): **{delta_str}** *(без нормы)*"
                    elif delta_h >= DAILY_NORM_HOURS:
                        status = f"🟢 `{nick}` ({pos}): **{delta_str}** / 1ч ✅"
                    else:
                        status = f"🔴 `{nick}` ({pos}): **{delta_str}** / 1ч ⏳"
                    lines.append(status)

                embed.add_field(name="📋 Состав персонала", value="\n".join(lines)[:4000], inline=False)
                embed.set_footer(text="WeriGrief Staff System • Режим онлайн-мониторинга")
                await ctx.send(embed=embed)
                return
            else:
                target = ctx.author.mention

        mc_nick, member = self.staff_resolver.resolve_query_to_target(guild, target)

        if not admin_caller and author_staff:
            author_nick = (author_staff.get("nick") or "").lower()
            if mc_nick.lower() != author_nick and (member is None or member.id != ctx.author.id):
                await ctx.send("❌ Вы можете просматривать только свою суточную норму онлайна. Администраторы могут проверять любого сотрудника.", ephemeral=True)
                return

        info = await self.playtime_tracker.get_staff_norm_info(mc_nick)
        if not info:
            await ctx.send(f"❌ Не удалось получить данные по онлайну для `{mc_nick}`.")
            return

        if info["is_exempt"]:
            color = 0x3498DB
            status_text = "👑 **Освобожден от обязательной нормы** (руководство / решение администрации)"
        elif info["norm_met"]:
            color = 0x2ECC71
            status_text = f"✅ **Суточная норма выполнена!** ({info['today_str']} / 1ч 00м)"
        else:
            color = 0xE67E22 if info["percent"] >= 50 else 0xE74C3C
            status_text = f"⏳ **В процессе выполнения** ({info['today_str']} / 1ч 00м, осталось **{info['remaining_str']}**)"

        embed = discord.Embed(
            title=f"⏱ Суточная норма онлайна: {info['nick']}",
            color=color,
            description=status_text,
            timestamp=datetime.now(timezone.utc),
        )

        display_name = f"`{info['nick']}`"
        if member:
            display_name += f" ({member.mention})"

        embed.add_field(name="👤 Сотрудник", value=display_name, inline=True)
        embed.add_field(name="📌 Должность", value=info["position"], inline=True)
        embed.add_field(name="⚠️ Активных варнов", value=f"**{info['warn_count']}** шт.", inline=True)

        embed.add_field(
            name="📈 Прогресс нормы за сегодня",
            value=f"`{info['progress_bar']}` **{info['percent']}%** ({info['today_str']} из 1ч)",
            inline=False
        )
        embed.add_field(name="⏱ Отыграно сегодня (с 00:00 MSK)", value=f"**{info['today_str']}** / 1ч 00м", inline=True)
        embed.add_field(name="📑 Всего часов в таблице", value=f"**{info['sheet_hours']} ч.**", inline=True)
        embed.add_field(name="🎮 Общий онлайн на сервере", value=f"**{info['lifetime_str']}**", inline=True)

        embed.set_footer(text="WeriGrief Staff System • Сброс суточного счетчика в 00:00 MSK")
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="norm_exempt",
        aliases=["exempt_norm", "toggle_norm"],
        description="Отключить или включить суточную норму онлайна (1ч) для выбранного администратора",
    )
    @app_commands.describe(
        target="Никнейм в игре или @Discord администратора",
        action="Действие: 'отключить' (освободить от нормы) или 'включить' (вернуть норму)"
    )
    async def cmd_norm_exempt(self, ctx: commands.Context, target: str, action: str = "отключить"):
        """Включает или отключает индивидуальное освобождение от суточной нормы онлайна."""
        await ctx.defer()
        guild = ctx.guild or self.bot.get_guild(GUILD_ID)
        if not guild:
            await ctx.send("❌ Сервер не найден.")
            return

        if not self.staff_resolver.is_admin_or_higher(ctx.author, guild):
            await ctx.send("❌ Команда доступна только **Администраторам**, **Старшим администраторам** и руководству проекта.", ephemeral=True)
            return

        if not self.sheets_monitor:
            await ctx.send("❌ Система Google Sheets еще инициализируется.")
            return

        mc_nick, member = self.staff_resolver.resolve_query_to_target(guild, target)
        act = action.strip().lower()
        exempt = act in ["отключить", "disable", "off", "освободить", "снять"]

        self.sheets_monitor.set_norm_exempt(mc_nick, is_exempt=exempt)
        display_name = f"`{mc_nick}`" + (f" ({member.mention})" if member else "")

        if exempt:
            embed = discord.Embed(
                title="👑 Суточная норма онлайна отключена",
                color=0x3498DB,
                description=(
                    f"Администратор {ctx.author.mention} **освободил** сотрудника {display_name} от обязательной суточной нормы онлайна (1 час).\n\n"
                    f"ℹ️ В 00:00 MSK автоматические предупреждения за онлайн начисляться не будут."
                ),
                timestamp=datetime.now(timezone.utc),
            )
        else:
            embed = discord.Embed(
                title="⏱ Суточная норма онлайна включена",
                color=0x2ECC71,
                description=(
                    f"Администратор {ctx.author.mention} **включил** обязательную суточную норму онлайна (1 час) для сотрудника {display_name}.\n\n"
                    f"ℹ️ Сотрудник обязан наигрывать 1 час в сутки."
                ),
                timestamp=datetime.now(timezone.utc),
            )

        embed.set_footer(text=f"Изменил: {ctx.author} • WeriGrief Staff System")
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="help_staff",
        aliases=["staffhelp", "ahelp"],
        description="Справочник всех команд для персонала и администрации",
    )
    async def cmd_help_staff(self, ctx: commands.Context):
        """Справочное меню со всеми командами персонала."""
        await ctx.defer()
        author_staff = self.staff_resolver.get_staff_entry_for_member(ctx.author)
        if not author_staff and ctx.author.id not in LEADERSHIP_IDS:
            await ctx.send(
                "❌ **Доступ запрещен:** вы не числитесь в официальной таблице персонала (Google Sheets).\n"
                "Наличие роли или прав в Discord без записи в таблице не дает права на использование команд персонала.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🛡️ Справочник команд WeriGrief Staff System",
            color=0x5865F2,
            description="Полный список доступных команд для сотрудников и администрации проекта.",
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="👑 Управление предупреждениями",
            value=(
                "• `/warn <цель> <причина>` — выдать предупреждение сотруднику с записью в таблицу *(Администрация)*\n"
                "• `/unwarn <цель> [номер/причина]` — снять предупреждение (номер из `/warns` или последнее) *(Администрация)*\n"
                "• `/warns [цель]` — посмотреть активные предупреждения сотрудника и их причины\n"
                "• `/clearwarns <цель>` — полностью аннулировать все предупреждения сотрудника *(Руководство)*"
            ),
            inline=False
        )

        embed.add_field(
            name="⏱ Контроль нормы онлайна (1 час)",
            value=(
                "• `/norm [цель]` — просмотр суточной нормы онлайна за день (чистая разница с 00:00 MSK)\n"
                "• `/norm_exempt <цель> [отключить/включить]` — отключить или вернуть норму онлайна для выбранного администратора *(Администрация)*\n"
                "• `/check_norm [dry_run]` — принудительно запустить суточную проверку нормы онлайна *(Администрация)*"
            ),
            inline=False
        )

        embed.add_field(
            name="👥 Управление составом персонала",
            value=(
                "• `/staff` — актуальный список администрации сервера из Google Таблицы\n"
                "• `/appoint <цель> <должность>` — назначить сотрудника в персонал (выдача ролей Discord и прав в Minecraft) *(Руководство)*\n"
                "• `/unstaff <цель> [причина]` — снять сотрудника с должности персонала (снятие ролей и прав) *(Руководство)*\n"
                "• `/dismissed` — архивный журнал учета снятого персонала («Админ пока») *(Руководство)*"
            ),
            inline=False
        )

        embed.add_field(
            name="ℹ️ Информация о доступе",
            value=(
                "• **Сотрудники:** просмотр своей нормы (`/norm`), просмотр варнов (`/warns`), список стаффа (`/staff`).\n"
                "• **Администраторы и Ст.Администраторы:** выдача и снятие варнов (`/warn`, `/unwarn`), отключение нормы (`/norm_exempt`), проверка нормы любого сотрудника (`/norm`).\n"
                "• **Руководство (Владельцы, Со-владельцы, Кураторы):** полный доступ ко всем функциям."
            ),
            inline=False
        )

        embed.set_footer(text="WeriGrief Staff System • Префикс: ! / Слэш: /")
        await ctx.send(embed=embed)

