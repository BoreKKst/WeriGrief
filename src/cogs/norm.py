"""
Cog for playtime and norm monitoring commands (/norm, /check_norm, /norm_exempt, /help_staff).
"""
from zoneinfo import ZoneInfo


from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands
from src.core.config import GUILD_ID, DAILY_NORM_HOURS, LEADERSHIP_IDS, STAFF_NOTIFY_CHANNEL


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
        description="Проверить суточную норму персонала",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="Без предупреждений", value="без пред"),
        app_commands.Choice(name="С предупреждениями", value="предом"),
    ])
    async def cmd_check_norm(
        self,
        ctx: commands.Context,
        mode: app_commands.Choice[str],
    ):
        """Ручной запуск проверки суточной нормы онлайна."""
        await ctx.defer()
        guild = ctx.guild or self.bot.get_guild(GUILD_ID)
        if not self.staff_resolver.is_admin_or_higher(ctx.author, guild):
            await ctx.send("❌ Команда доступна только **Администраторам**, **Старшим администраторам** и руководству проекта.", ephemeral=True)
            return

        # Ручная проверка не начисляет часы и не закрывает сутки.
        # Предупреждения разрешаются отдельным флагом и только при reliable=True.
        issue_warnings = mode.value == "предом"
        res = await self.playtime_tracker.process_daily_playtime_and_norms(
            guild,
            dry_run=True,
            is_midnight=False,
            send_summary=True,
            issue_warnings=issue_warnings,
        )
        if res.get("error"):
            await ctx.send(f"❌ {res['error']}")
            return
        if not res.get("summary_sent"):
            detail = res.get("summary_error") or "Канал Персонал не настроен или недоступен."
            await ctx.send(
                f"⚠️ Проверка завершена, но отчёт НЕ отправлен в канал Персонал.\n{detail}\n"
                f"Результат: всего **{res.get('total', 0)}**, выполнили **{res.get('met', 0)}**, "
                f"не выполнили **{res.get('failed', 0)}**, освобождены **{res.get('exempt', 0)}**.\n"
            )
            return

        await ctx.send("✅ Проверка нормы выполнена.", ephemeral=True)

    @commands.hybrid_command(
        name="norm",
        aliases=["norma", "playtime_norm"],
        description="Просмотр суточной нормы онлайна сотрудника (чистый онлайн за сегодня с 00:00 MSK)",
    )
    @app_commands.describe(target="Никнейм в игре или @Discord сотрудника (если пусто — ваша норма)")
    async def cmd_norm(self, ctx: commands.Context, *, target: str = None):
        """Отображает текущий прогресс суточной нормы только выбранного сотрудника."""
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

        # Без аргумента /norm ВСЕГДА показывает только вызывающего команду.
        # Общий отчёт по персоналу предназначен исключительно для /check_norm.
        if not target:
            if not author_staff or not author_staff.get("nick"):
                await ctx.send(
                    "❌ Ваш Minecraft-ник не найден в Google Sheets (лист «Списки»). "
                    "Добавьте себя в таблицу или укажите сотрудника, если у вас есть права.",
                    ephemeral=True,
                )
                return
            target = author_staff["nick"]

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
        elif info.get("norm_not_started", False):
            color = 0x3498DB
            status_text = "🆕 **Норма начнётся завтра!** Сегодня — день вступления в состав, выполнять суточную норму пока не требуется."
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

        embed.set_footer(text="WeriTime Staff System • Сброс суточного счетчика в 00:00 MSK")
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

        embed.set_footer(text=f"Изменил: {ctx.author} • WeriTime Staff System")
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="help_staff",
        aliases=["staffhelp", "ahelp"],
        description="Справочник всех команд для персонала и администрации",
    )
    async def cmd_help_staff(self, ctx: commands.Context):
        """Общий справочник доступных команд бота."""
        await ctx.defer()
        embed = discord.Embed(
            title="🛡️ Команды WeriTime",
            color=0x5865F2,
            description="Справочник команд. Бот проверяет права отдельно при каждом вызове.",
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="👑 Управление предупреждениями",
            value=(
                "• `/warn <цель> <причина>` — выдать предупреждение сотруднику с записью в таблицу *(Администрация)*\n"
                "• `/unwarn <цель> [номер/причина]` — снять предупреждение (номер из `/warns` или последнее) *(Администрация)*\n"
                "• `/warns [цель]` — посмотреть активные предупреждения сотрудника и их причины\n"
                "• `/clearwarns <цель>` — полностью аннулировать все предупреждения сотрудника *(Руководство)*\n"
                "• Автомодерация: мат и оскорбления удаляются, автор получает тайм-аут на 10 минут."
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
                "• `/promote` и `/demote` — изменить должность и синхронизировать роли *(Руководство)*\n"
                "• `/unstaff <цель> [причина]` — снять сотрудника с должности персонала (снятие ролей и прав) *(Руководство)*\n"
                "• `/vacation <дни>` — оформить отпуск на 1–15 дней *(сотрудник)*\n"
                "• `/unvacation [ник]` — завершить свой отпуск; чужой отпуск может завершить администрация\n"
                "• `/dismissed` — архивный журнал учета снятого персонала («Админ пока») *(Руководство)*"
            ),
            inline=False
        )

        embed.add_field(
            name="🤖 ИИ и комнаты",
            value=(
                "• В канале `🤖・ai-помощник` — помощь с кодом, вопросами и домашними заданиями; можно прикрепить изображение для разбора.\n"
                "• `/image <описание>` — создать изображение по текстовому запросу.\n"
                "• `/make_file <имя> <задача>` — создать текстовый файл с кодом или документом.\n"
                "• `/create_room` — создать личную голосовую комнату *(участники сервера)*\n"
                "• `/room_invite <участник>` и `/room_revoke <участник>` — управлять доступом к своей комнате"
            ),
            inline=False,
        )

        embed.add_field(
            name="📨 Заявки и поддержка",
            value=(
                "В `📝・набор` выбирают Хелпера или Медиа; в `🛠️・поддержка` — вопрос, баг или жалобу. "
                "Для жалобы укажи, на администрацию она или участника. Каждый тикет создаётся в отдельном приватном канале; туда можно прикладывать фото."
            ),
            inline=False,
        )

        embed.add_field(
            name="🎮 Кланы, экономика и игры",
            value=(
                "`/clan_create`, `/clan_invite`, `/clan_leave`, `/clan_disband`, `/clan_info` — создать клан, приглашать в ЛС и управлять составом. "
                "Тег клана отображается рядом с ником, если у бота есть право менять никнеймы.\n"
                "`/balance`, `/daily`, `/work`, `/shop`, `/buy`, `/inventory`, `/transfer` — экономика и магазин из 15 предметов.\n"
                "`/guess_character`, `/guess_leaderboard`, `/joke` — мини-игра, таблица побед и шутки."
            ),
            inline=False,
        )

        embed.add_field(
            name="ℹ️ Информация о доступе",
            value=(
                "• **Участники:** `/help_staff`, `/create_room`, ИИ-помощник; автомодерация работает для всех пользователей.\n"
                "• **Сотрудники из Google Sheets:** `/staff`, `/warns`, `/vacation`, просмотр своей нормы (`/norm`).\n"
                "• **Администраторы и Ст.Администраторы:** выдача и снятие варнов (`/warn`, `/unwarn`), отключение нормы (`/norm_exempt`), проверка нормы любого сотрудника (`/norm`).\n"
                "• **Руководство (Владельцы, Со-владельцы, Кураторы):** полный доступ ко всем функциям."
            ),
            inline=False
        )

        embed.set_footer(text="WeriTime Staff System • Префикс: ! / Слэш: /")
        await ctx.send(embed=embed)

