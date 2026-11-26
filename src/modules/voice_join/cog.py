import logging

import discord
from discord.ext import commands

log = logging.getLogger("weritime.voice_join")

OWNER_ID = 1374053953301385308


class VoiceJoinCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        log.info("VOICE_JOIN_COG_INIT")

    @commands.command(name="зайди")
    async def join_voice(self, ctx, channel_id: int):
        if ctx.guild is not None:
            return await ctx.reply(
                "Эту секретную команду нужно писать боту в ЛС."
            )

        if ctx.author.id != OWNER_ID:
            log.warning(
                "JOIN_REJECTED user=%s reason=not_owner",
                ctx.author.id,
            )
            return await ctx.reply("Нет доступа.")

        log.info(
            "OWNER_JOIN_REQUEST owner=%s channel=%s",
            ctx.author.id,
            channel_id,
        )

        target = None

        for guild in self.bot.guilds:
            channel = guild.get_channel(channel_id)
            if isinstance(channel, discord.VoiceChannel):
                target = channel
                break

        if target is None:
            return await ctx.reply(
                "Голосовой канал с таким ID не найден."
            )

        try:
            voice = target.guild.voice_client

            if voice and voice.is_connected():
                if voice.channel.id == target.id:
                    return await ctx.reply(
                        f"Уже нахожусь в канале **{target.name}**."
                    )
                await voice.move_to(target)
            else:
                await target.connect()

            log.info(
                "OWNER_JOIN_SUCCESS guild=%s channel=%s",
                target.guild.id,
                target.id,
            )

            await ctx.reply(
                f"Подключился к голосовому каналу **{target.name}**."
            )

        except Exception:
            log.exception(
                "OWNER_JOIN_FAILED channel=%s",
                channel_id,
            )
            await ctx.reply(
                "Не удалось подключиться. Подробности записаны в лог."
            )


async def setup(bot):
    await bot.add_cog(VoiceJoinCog(bot))
