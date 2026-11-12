"""
Cog for managing warnings (/warn, /unwarn, /warns, /clearwarns).
"""

from datetime import datetime, timezone, timedelta
import discord
from discord import app_commands
from discord.ext import commands
from src.core.config import log, GUILD_ID, WARN_NOTIFY_CHANNEL, ADMIN_KEYWORDS, LEADERSHIP_ROLES, LEADERSHIP_IDS, STAFF_REGISTRY
from src.utils.embeds import EmbedBuilder
from src.services.sheets_api import normalize_date_str


class WarningsCog(commands.Cog, name="Warnings"):
    """Команды управления предупреждениями персонала."""

    def __init__(self, bot, sheets_monitor, staff_resolver):
        self.bot = bot
        self.sheets_monitor = sheets_monitor
        self.staff_resolver = staff_resolver

    @commands.hybrid_command(
        name="warn",
        description="Выдать предупреждение сотруднику с записью в таблицу (для Администрации)",
    )
    @app_commands.describe(
        target="Никнейм в игре, @Discord или тег сотрудника",
        reason="Причина предупреждения (например: Неисполнение нормы)",
    )
    async def cmd_warn(self, ctx: commands.Context, target: str, *, reason: str):
        """Выдаёт предупреждение сотруднику с записью в Google Sheets."""
        await ctx.defer()
        guild = ctx.guild or self.bot.get_guild(GUILD_ID)
        if not guild:
            await ctx.send("❌ Ошибка: сервер не найден.")
            return

        if not self.sheets_monitor:
            await ctx.send("❌ Система Google Sheets еще инициализируется, попробуйте через минуту.")
            return

        author_staff = self.staff_resolver.get_staff_entry_for_member(ctx.author)
        if not author_staff:
            await ctx.send(
                "❌ **Доступ запрещен:** вы не числитесь в официальной таблице персонала (Google Sheets).\n"
                "Наличие роли или прав в Discord без записи в таблице не дает права на выдачу предупреждений.",
                ephemeral=True,
            )
            return

        author_pos = (author_staff.get("position") or "").lower()
        is_auth_pos = any(k in author_pos for k in ADMIN_KEYWORDS) or any(k in author_pos for k in LEADERSHIP_ROLES)
        if not is_auth_pos:
            await ctx.send(
                f"❌ **Недостаточно полномочий:** ваша должность в таблице персонала (**{author_staff.get('position', '—')}**) "
                f"не дает права на выдачу предупреждений.\n"
                f"Команда `/warn` доступна только Администраторам, Старшим администраторам и руководству проекта согласно таблице.",
                ephemeral=True,
            )
            return

        mc_nick, member = self.staff_resolver.resolve_query_to_target(guild, target)

        author_id = ctx.author.id
        if author_staff.get("nick"):
            issuer_name = author_staff.get("nick")
        else:
            for mc, info in STAFF_REGISTRY.items():
                if info.get("discord_id") == author_id:
                    issuer_name = mc
                    break
            else:
                issuer_name = ctx.author.display_name or ctx.author.name

        date_str = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%d.%m.%Y")
        total_warns = self.sheets_monitor.add_warning(mc_nick, date_str, reason, issuer_name)

        warn_channel = guild.get_channel(WARN_NOTIFY_CHANNEL)
        ping_text = f"{member.mention} " if member else ""
        embed = EmbedBuilder.build_warn_embed(mc_nick, date_str, reason, issuer_name, total_warns)
        if warn_channel:
            await warn_channel.send(content=ping_text, embed=embed)

        role_assigned = False
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
                    role_assigned = True
                except Exception as e:
                    log.error(f"Failed to assign role to {member}: {e}")

        resp_desc = f"Предупреждение сотруднику `{mc_nick}` успешно занесено в таблицу!\n**Всего предупреждений:** {total_warns}"
        if role_assigned:
            resp_desc += "\n🚨 Назначена роль `⚠️ На испытании`."

        await ctx.send(f"✅ {resp_desc}")

    @commands.hybrid_command(
        name="unwarn",
        aliases=["removewarn", "delwarn"],
        description="Снять предупреждение с сотрудника из таблицы (по номеру или последнее)",
    )
    @app_commands.describe(
        target="Никнейм в игре, @Discord или тег сотрудника",
        case="Номер предупреждения из /warns (например: 1, 2) или причина (если пусто — последнее)",
    )
    async def cmd_unwarn(self, ctx: commands.Context, target: str, *, case: str = ""):
        """Снимает конкретное предупреждение с сотрудника из Google Sheets."""
        await ctx.defer()
        guild = ctx.guild or self.bot.get_guild(GUILD_ID)
        if not guild:
            await ctx.send("❌ Сервер не найден.")
            return

        if not self.sheets_monitor:
            await ctx.send("❌ Система Google Sheets еще инициализируется, попробуйте через минуту.")
            return

        author_staff = self.staff_resolver.get_staff_entry_for_member(ctx.author)
        if not author_staff:
            await ctx.send(
                "❌ **Доступ запрещен:** вы не числитесь в официальной таблице персонала (Google Sheets).\n"
                "Наличие роли или прав в Discord без записи в таблице не дает права на снятие предупреждений.",
                ephemeral=True,
            )
            return

        author_pos = (author_staff.get("position") or "").lower()
        is_auth_pos = any(k in author_pos for k in ADMIN_KEYWORDS) or any(k in author_pos for k in LEADERSHIP_ROLES)
        if not is_auth_pos:
            await ctx.send(
                f"❌ **Недостаточно полномочий:** ваша должность в таблице персонала (**{author_staff.get('position', '—')}**) "
                f"не дает права на снятие предупреждений.\n"
                f"Команда `/unwarn` доступна только Администраторам, Старшим администраторам и руководству проекта согласно таблице.",
                ephemeral=True,
            )
            return

        mc_nick, member = self.staff_resolver.resolve_query_to_target(guild, target)

        case_clean = case.strip()
        case_index = None
        reason_kw = None
        if case_clean:
            digits = case_clean.lstrip("#")
            if digits.isdigit():
                case_index = int(digits)
            else:
                reason_kw = case_clean

        success, removed_info, remaining_count = self.sheets_monitor.remove_warning(
            mc_nick, case_index=case_index, reason_keyword=reason_kw
        )

        if not success or not removed_info:
            await ctx.send(f"ℹ️ У игрока `{mc_nick}` не найдено предупреждений для снятия (или указан неверный номер/причина). Проверьте `/warns {mc_nick}`.")
            return

        role_removed = False
        if member and remaining_count < 3:
            warn_role = discord.utils.get(guild.roles, name="⚠️ На испытании")
            if warn_role and warn_role in member.roles:
                try:
                    await member.remove_roles(warn_role, reason=f"Варн снят администратором {ctx.author}")
                    role_removed = True
                except Exception as e:
                    log.error(f"Failed to remove probation role from {member}: {e}")

        warn_channel = guild.get_channel(WARN_NOTIFY_CHANNEL)
        embed = discord.Embed(
            title="✅ Предупреждение снято администрацией",
            color=0x2ECC71,
            description=f"Администратор {ctx.author.mention} аннулировал предупреждение сотрудника `{mc_nick}`.",
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="👤 Сотрудник", value=f"`{mc_nick}`" + (f" ({member.mention})" if member else ""), inline=True)
        embed.add_field(name="🗑️ Снятый варн", value=f"#{removed_info['case_num']} от `{normalize_date_str(removed_info['date'])}`", inline=True)
        embed.add_field(name="🔢 Осталось варнов", value=f"**{remaining_count}** шт.", inline=True)
        embed.add_field(name="📋 Причина снятого варна", value=removed_info['reason'], inline=False)
        if role_removed:
            embed.add_field(name="🎉 Роль испытательного срока", value="Снята с сотрудника (варнов менее 3)", inline=False)
        embed.set_footer(text=f"Снял: {ctx.author} • WeriGrief Staff System")

        ping_text = f"{member.mention} " if member else ""
        if warn_channel:
            await warn_channel.send(content=ping_text, embed=embed)

        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="warns",
        description="Посмотреть предупреждения сотрудника (по нику, Discord, тегу или почте)",
    )
    @app_commands.describe(target="Никнейм в игре, упоминание @Discord, Discord-тег или почта")
    async def cmd_warns(self, ctx: commands.Context, *, target: str = None):
        """Отображает список предупреждений сотрудника."""
        await ctx.defer()

        if not self.sheets_monitor:
            await ctx.send("❌ Система Google Sheets еще инициализируется, попробуйте через минуту.")
            return

        guild = ctx.guild or self.bot.get_guild(GUILD_ID)
        if not guild:
            await ctx.send("❌ Ошибка: сервер не найден.")
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
            target = ctx.author.mention

        mc_nick, member = self.staff_resolver.resolve_query_to_target(guild, target)

        if not admin_caller and author_staff:
            author_nick = (author_staff.get("nick") or "").lower()
            if mc_nick.lower() != author_nick and (member is None or member.id != ctx.author.id):
                await ctx.send("❌ Вы можете просматривать только свои предупреждения. Администраторы могут проверять любого сотрудника.", ephemeral=True)
                return

        try:
            data = self.sheets_monitor.get_warnings_for(mc_nick)
        except Exception as e:
            log.error(f"Error fetching warns for {mc_nick}: {e}")
            await ctx.send(f"❌ Ошибка при чтении данных: {e}")
            return

        warns = data["warnings"]
        total_count = len(warns)
        sheet_count = data.get("sheet_warn_count", total_count)
        staff_info = data.get("staff_info")

        display_name = data["nick"]
        if member:
            display_name = f"`{data['nick']}` ({member.mention})"

        if total_count == 0:
            desc = f"✅ У сотрудника {display_name} **нет активных предупреждений**."
            if staff_info:
                desc += f"\n📌 **Должность:** {staff_info.get('position', '—')}"
            embed = discord.Embed(
                title=f"📋 Предупреждения: {data['nick']}",
                color=0x2ECC71,
                description=desc,
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(text="WeriGrief Staff System")
            await ctx.send(embed=embed)
            return

        embed_color = 0xFFA500 if total_count == 1 else (0xFF6600 if total_count == 2 else 0xE74C3C)
        embed = discord.Embed(
            title=f"📋 Предупреждения: {data['nick']}",
            color=embed_color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="👤 Сотрудник", value=display_name, inline=True)
        embed.add_field(name="🔢 Всего варнов", value=f"**{total_count}** (по таблице: {sheet_count})", inline=True)

        if staff_info:
            embed.add_field(name="📌 Должность", value=staff_info.get("position", "—"), inline=True)

        if member:
            warn_role = discord.utils.get(guild.roles, name="⚠️ На испытании")
            has_role = warn_role in member.roles if warn_role else False
            role_status = "🔴 Активна" if has_role else ("🟡 Не выдана" if total_count >= 3 else "🟢 В норме")
            embed.add_field(name="⚠️ Роль испытательного срока", value=role_status, inline=True)

        for i, w in enumerate(warns, 1):
            embed.add_field(
                name=f"#{i} — {normalize_date_str(w['date'])}",
                value=f"**Причина:** {w['reason']}\n**Выдал:** `{w['issued_by']}`",
                inline=False,
            )

        embed.set_footer(text="WeriGrief Staff System • Префикс: !warns / Слэш: /warns")
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="clearwarns",
        description="Снять предупреждения с сотрудника и очистить в таблице (для руководства)",
    )
    @app_commands.describe(target="Никнейм в игре, @Discord или тег сотрудника")
    async def cmd_clearwarns(self, ctx: commands.Context, *, target: str):
        """Очищает все предупреждения сотрудника в таблице (для руководства)."""
        await ctx.defer()
        guild = ctx.guild or self.bot.get_guild(GUILD_ID)
        if not guild:
            await ctx.send("❌ Сервер не найден.")
            return

        if not self.sheets_monitor:
            await ctx.send("❌ Система Google Sheets еще инициализируется, попробуйте через минуту.")
            return

        author_staff = self.staff_resolver.get_staff_entry_for_member(ctx.author)
        if not author_staff:
            await ctx.send(
                "❌ **Доступ запрещен:** вы не числитесь в официальной таблице персонала (Google Sheets).\n"
                "Наличие роли или прав в Discord без записи в таблице не дает права на аннулирование предупреждений.",
                ephemeral=True,
            )
            return

        author_pos = (author_staff.get("position") or "").lower()
        is_lead_pos = any(k in author_pos for k in LEADERSHIP_ROLES)
        if not is_lead_pos:
            await ctx.send(
                f"❌ **Недостаточно полномочий:** ваша должность в таблице персонала (**{author_staff.get('position', '—')}**) "
                f"не дает права на полную очистку предупреждений.\n"
                f"Команда `/clearwarns` доступна только Владельцам, Со-владельцам и Кураторам проекта согласно таблице.",
                ephemeral=True,
            )
            return

        mc_nick, member = self.staff_resolver.resolve_query_to_target(guild, target)
        removed = self.sheets_monitor.clear_player_warnings(mc_nick)
        if removed == 0:
            await ctx.send(f"ℹ️ У игрока `{mc_nick}` не найдено предупреждений в таблице для снятия.")
            return

        role_removed = False
        if member:
            warn_role = discord.utils.get(guild.roles, name="⚠️ На испытании")
            if warn_role and warn_role in member.roles:
                try:
                    await member.remove_roles(warn_role, reason=f"Варны сняты администратором {ctx.author}")
                    role_removed = True
                except Exception as e:
                    log.error(f"Failed to remove probation role from {member}: {e}")

        embed = discord.Embed(
            title="✅ Предупреждения сняты руководящим составом",
            color=0x2ECC71,
            description=f"Администратор {ctx.author.mention} аннулировал предупреждения сотрудника `{mc_nick}`.",
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="👤 Сотрудник", value=f"`{mc_nick}`" + (f" ({member.mention})" if member else ""), inline=True)
        embed.add_field(name="📉 Удалено записей", value=f"**{removed}** шт.", inline=True)
        embed.add_field(name="🔢 Текущий остаток", value="**0**", inline=True)

        if role_removed:
            embed.add_field(name="🎉 Роль испытательного срока", value="Снята с сотрудника", inline=False)

        embed.set_footer(text="WeriGrief Staff System • Google Sheets Sync")
        await ctx.send(embed=embed)

