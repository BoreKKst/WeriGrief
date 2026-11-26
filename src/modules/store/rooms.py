import logging
import re

import discord
from discord.ext import commands

from src.core.database import get_pool

ROOM_CATEGORY_ID = 1571415009303461970
ROOM_CATEGORY_NAME = "🎙・личные-комнаты"
ROOM_TRIGGER_NAME = "➕・создать-комнату"

log = logging.getLogger("weritime.rooms")


class RoomRenameModal(discord.ui.Modal, title="Название комнаты"):
    name = discord.ui.TextInput(label="Новое название", max_length=90)
    def __init__(self, channel):
        super().__init__(); self.channel = channel
    async def on_submit(self, interaction: discord.Interaction):
        await self.channel.edit(name=str(self.name).strip(), reason="Creator изменил название комнаты")
        await interaction.response.send_message("✅ Название комнаты изменено.", ephemeral=True)

class RoomLimitModal(discord.ui.Modal, title="Лимит участников"):
    limit = discord.ui.TextInput(label="Число участников (0 — без лимита)", max_length=2, default="0")
    def __init__(self, channel):
        super().__init__(); self.channel = channel
    async def on_submit(self, interaction: discord.Interaction):
        try: value = int(str(self.limit))
        except ValueError:
            await interaction.response.send_message("Укажи число от 0 до 99.", ephemeral=True); return
        if not 0 <= value <= 99:
            await interaction.response.send_message("Укажи число от 0 до 99.", ephemeral=True); return
        await self.channel.edit(user_limit=value, reason="Creator изменил лимит комнаты")
        await interaction.response.send_message("✅ Лимит обновлён.", ephemeral=True)

class RoomControlView(discord.ui.View):
    def __init__(self, channel_id: int, owner_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.owner_id = owner_id
        for label, emoji, style, action in (
            ("Переименовать", "✏️", discord.ButtonStyle.primary, self.rename_room),
            ("Лимит", "👥", discord.ButtonStyle.secondary, self.change_limit),
            ("Закрыть / открыть", "🔒", discord.ButtonStyle.secondary, self.toggle_lock),
            ("Удалить комнату", "🗑️", discord.ButtonStyle.danger, self.delete_room),
        ):
            button = discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=f"weritime:room:{action.__name__}:{channel_id}:{owner_id}",
            )
            button.callback = action
            self.add_item(button)

    async def get_channel(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Это меню доступно только владельцу комнаты.", ephemeral=True); return None
        channel = interaction.client.get_channel(self.channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message("Комната уже не существует.", ephemeral=True); return None
        return channel
    async def rename_room(self, interaction):
        channel = await self.get_channel(interaction)
        if channel:
            await interaction.response.send_modal(RoomRenameModal(channel))

    async def change_limit(self, interaction):
        channel = await self.get_channel(interaction)
        if channel:
            await interaction.response.send_modal(RoomLimitModal(channel))

    async def toggle_lock(self, interaction):
        channel = await self.get_channel(interaction)
        if not channel:
            return
        everyone = channel.guild.default_role
        overwrite = channel.overwrites_for(everyone)
        overwrite.connect = False if overwrite.connect is not False else True
        await channel.set_permissions(everyone, overwrite=overwrite, reason="Владелец изменил доступ к комнате")
        state = "закрыта" if overwrite.connect is False else "открыта"
        await interaction.response.send_message(f"✅ Комната {state}.", ephemeral=True)

    async def delete_room(self, interaction):
        channel = await self.get_channel(interaction)
        if not channel:
            return
        await interaction.response.send_message(
            "Комната будет удалена. Подтвердить?",
            view=DeleteRoomConfirmView(channel.id, self.owner_id),
            ephemeral=True,
        )


class DeleteRoomConfirmView(discord.ui.View):
    def __init__(self, channel_id: int, owner_id: int):
        super().__init__(timeout=30)
        self.channel_id = channel_id
        self.owner_id = owner_id

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Удалить комнату может только её владелец.", ephemeral=True)
            return
        channel = interaction.client.get_channel(self.channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message("Комната уже удалена.", ephemeral=True)
            return
        async with get_pool().acquire() as conn:
            await conn.execute(
                "DELETE FROM store.personal_voice_rooms WHERE channel_id=$1 AND owner_id=$2",
                channel.id, self.owner_id,
            )
        await channel.delete(reason="Владелец комнаты подтвердил удаление")
        await interaction.response.send_message("✅ Комната удалена.", ephemeral=True)


class PersonalRoomsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.categories = {}
        self.triggers = {}

    async def cog_load(self):
        try:
            await self.ensure_room_setup()
        except discord.HTTPException:
            log.exception("Could not prepare personal rooms")

    async def ensure_room_setup(self):
        for guild in self.bot.guilds:
            bot_member = guild.me
            if not bot_member or not bot_member.guild_permissions.manage_channels:
                log.warning("Cannot prepare personal rooms in %s: bot needs Manage Channels", guild.id)
                continue
            category = guild.get_channel(ROOM_CATEGORY_ID)
            if not isinstance(category, discord.CategoryChannel):
                category = discord.utils.get(guild.categories, name=ROOM_CATEGORY_NAME)
            if category is None:
                category = await guild.create_category(ROOM_CATEGORY_NAME, reason="Настройка личных комнат WeriTime")
            elif category.name != ROOM_CATEGORY_NAME:
                await category.edit(name=ROOM_CATEGORY_NAME, reason="Оформление категории личных комнат WeriTime")
            self.categories[guild.id] = category
            trigger = discord.utils.get(guild.voice_channels, name=ROOM_TRIGGER_NAME)
            if trigger is None:
                trigger = await guild.create_voice_channel(
                    ROOM_TRIGGER_NAME,
                    category=category,
                    user_limit=1,
                    overwrites={
                        guild.default_role: discord.PermissionOverwrite(
                            view_channel=True, connect=True, speak=False,
                        ),
                        bot_member: discord.PermissionOverwrite(view_channel=True, connect=True, move_members=True),
                    },
                    reason="Канал входа для создания личных комнат",
                )
            elif trigger.category_id != category.id:
                await trigger.edit(category=category, reason="Возврат входного канала личных комнат в категорию WeriTime")
            self.triggers[guild.id] = trigger.id

        # Re-register each room's unique persistent control panel after restart.
        try:
            async with get_pool().acquire() as conn:
                await self.ensure_table(conn)
                rows = await conn.fetch("SELECT guild_id, channel_id, owner_id FROM store.personal_voice_rooms")
            for row in rows:
                guild = self.bot.get_guild(row["guild_id"])
                channel = guild.get_channel(row["channel_id"]) if guild else None
                category = self.categories.get(row["guild_id"])
                if guild and channel is None:
                    async with get_pool().acquire() as conn:
                        await conn.execute(
                            "DELETE FROM store.personal_voice_rooms WHERE guild_id=$1 AND channel_id=$2",
                            row["guild_id"], row["channel_id"],
                        )
                    continue
                if isinstance(channel, discord.VoiceChannel) and category and channel.category_id != category.id:
                    await channel.edit(category=category, reason="Восстановление структуры личных комнат WeriTime")
                self.bot.add_view(RoomControlView(row["channel_id"], row["owner_id"]))
        except Exception:
            log.exception("Could not restore saved personal-room controls")

    async def ensure_table(self, conn):
        await conn.execute("CREATE SCHEMA IF NOT EXISTS store")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS store.personal_voice_rooms (
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL UNIQUE,
                owner_id BIGINT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW()),
                PRIMARY KEY (guild_id, owner_id)
            )
        """)

    async def create_for_member(self, guild, member):
        bot_member = guild.me
        category = self.categories.get(guild.id)
        if category is None or bot_member is None:
            raise RuntimeError("Категория личных комнат не готова.")
        pool = get_pool()
        created_channel = None
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                        f"personal_room:{guild.id}:{member.id}",
                    )
                    await self.ensure_table(conn)
                    existing = await conn.fetchrow(
                        "SELECT channel_id FROM store.personal_voice_rooms WHERE guild_id=$1 AND owner_id=$2 FOR UPDATE",
                        guild.id, member.id,
                    )
                    if existing:
                        channel = guild.get_channel(existing["channel_id"])
                        if channel:
                            return channel, False
                        await conn.execute(
                            "DELETE FROM store.personal_voice_rooms WHERE guild_id=$1 AND owner_id=$2",
                            guild.id, member.id,
                        )

                    safe_name = re.sub(r"[^a-zA-Zа-яА-ЯёЁ0-9_-]+", "-", member.display_name.lower()).strip("-")[:55]
                    safe_name = safe_name or str(member.id)
                    overwrites = {
                        guild.default_role: discord.PermissionOverwrite(
                            view_channel=True, connect=False,
                        ),
                        member: discord.PermissionOverwrite(
                            view_channel=True, connect=True, speak=True, stream=True, use_voice_activation=True,
                        ),
                        bot_member: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True),
                    }
                    created_channel = await guild.create_voice_channel(
                        name=f"🔊・{safe_name}",
                        category=category,
                        overwrites=overwrites,
                        reason=f"Личная комната WeriTime для {member.id}",
                    )
                    await conn.execute(
                        "INSERT INTO store.personal_voice_rooms (guild_id,channel_id,owner_id,created_at) VALUES ($1,$2,$3,EXTRACT(EPOCH FROM NOW()))",
                        guild.id, created_channel.id, member.id,
                    )
            return created_channel, True
        except Exception:
            if created_channel:
                try:
                    await created_channel.delete(reason="Откат неудачного создания комнаты")
                except discord.HTTPException:
                    log.exception("Could not remove a room after failed creation")
            raise

    async def send_control_panel(self, member, guild, channel):
        self.bot.add_view(RoomControlView(channel.id, member.id))
        try:
            await member.send(
                f"## 🎙 Твоя комната на сервере **{guild.name}**\n"
                f"Комната: {channel.mention}. Управляй названием, лимитом, доступом и удалением кнопками ниже.",
                view=RoomControlView(channel.id, member.id),
            )
            return True
        except discord.Forbidden:
            return False

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if channel.guild.id in self.categories and channel.name in {ROOM_CATEGORY_NAME, ROOM_TRIGGER_NAME}:
            self.categories.pop(channel.guild.id, None)
            self.triggers.pop(channel.guild.id, None)
            await self.ensure_room_setup()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot or not member.guild:
            return
        trigger_id = self.triggers.get(member.guild.id)
        if after.channel and after.channel.id == trigger_id:
            try:
                channel, created = await self.create_for_member(member.guild, member)
                await member.move_to(channel, reason="Создание личной комнаты через канал входа")
                if created and not await self.send_control_panel(member, member.guild, channel):
                    log.info("Could not DM personal-room control panel to %s", member.id)
            except Exception:
                log.exception("Could not create a room from the join-to-create channel")
                try:
                    await member.send("Не удалось создать комнату. Проверь права бота и попробуй ещё раз.")
                except discord.Forbidden:
                    pass

        if before.channel and before.channel.id != trigger_id and before.channel.id != getattr(after.channel, "id", None):
            try:
                async with get_pool().acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT channel_id FROM store.personal_voice_rooms WHERE guild_id=$1 AND channel_id=$2",
                        member.guild.id, before.channel.id,
                    )
                    if row and not before.channel.members:
                        await conn.execute("DELETE FROM store.personal_voice_rooms WHERE channel_id=$1", before.channel.id)
                        await before.channel.delete(reason="Автоудаление пустой личной комнаты")
            except Exception:
                log.exception("Could not clean up an empty personal room")

    @commands.hybrid_command(
        name="create_room",
        description="Создать бесплатную личную голосовую комнату"
    )
    async def create_room(self, ctx):
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.reply("Команда доступна только на сервере.")
            return

        guild = ctx.guild
        member = ctx.author
        bot_member = guild.me

        if bot_member is None or not bot_member.guild_permissions.manage_channels:
            await ctx.reply("Боту необходимо право «Управлять каналами».")
            return

        try:
            category = self.categories.get(guild.id)
            if category is None:
                await self.ensure_room_setup()
            created_channel, created = await self.create_for_member(guild, member)
        except discord.Forbidden:
            await ctx.reply("У бота недостаточно прав для создания комнаты.")
            return
        except Exception:
            log.exception("Ошибка создания личной комнаты")
            await ctx.reply("Не удалось создать комнату. Попробуй позже.")
            return

        if created:
            await self.send_control_panel(member, guild, created_channel)
        await ctx.reply(f"🔊 Твоя личная комната: {created_channel.mention}")

    async def _owned_room(self, member):
        async with get_pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT channel_id FROM store.personal_voice_rooms WHERE guild_id=$1 AND owner_id=$2",
                member.guild.id, member.id,
            )
        channel = member.guild.get_channel(row["channel_id"]) if row else None
        return channel if isinstance(channel, discord.VoiceChannel) else None

    @commands.hybrid_command(name="room_invite", description="Пригласить участника в свою личную комнату")
    async def room_invite(self, ctx, member: discord.Member):
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            await ctx.reply("Команда доступна только на сервере.")
            return
        channel = await self._owned_room(ctx.author)
        if not channel:
            await ctx.reply("Сначала создай свою комнату через `/create_room`.", ephemeral=True)
            return
        if member.bot:
            await ctx.reply("В личную комнату можно приглашать только участников.", ephemeral=True)
            return
        await channel.set_permissions(
            member,
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            reason=f"Приглашение владельцем комнаты {ctx.author.id}",
        )
        await ctx.reply(f"✅ {member.mention} приглашён(а) в {channel.mention}.", allowed_mentions=discord.AllowedMentions.none())

    @commands.hybrid_command(name="room_revoke", description="Отозвать доступ участника к своей комнате")
    async def room_revoke(self, ctx, member: discord.Member):
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            await ctx.reply("Команда доступна только на сервере.")
            return
        channel = await self._owned_room(ctx.author)
        if not channel:
            await ctx.reply("Сначала создай свою комнату через `/create_room`.", ephemeral=True)
            return
        if member.id == ctx.author.id:
            await ctx.reply("Владелец комнаты не может отозвать доступ у самого себя.", ephemeral=True)
            return
        await channel.set_permissions(member, overwrite=None, reason=f"Доступ отозван владельцем {ctx.author.id}")
        await ctx.reply(f"✅ Доступ для {member.mention} отозван.", allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(PersonalRoomsCog(bot))
