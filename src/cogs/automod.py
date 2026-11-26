import re
import unicodedata
from datetime import timedelta
import discord
from discord.ext import commands
from src.core.config import STAFF_NOTIFY_CHANNEL

# Нормализация визуально похожих символов: кириллица/латиница/цифры.
LOOKALIKE = str.maketrans({
    'а':'a','А':'a','в':'b','В':'b','с':'c','С':'c','е':'e','Е':'e','ё':'e','Ё':'e',
    'н':'h','Н':'h','к':'k','К':'k','м':'m','М':'m','о':'o','О':'o','р':'p','Р':'p',
    'т':'t','Т':'t','х':'x','Х':'x','у':'y','У':'y','і':'i','І':'i','з':'3','З':'3',
    '0':'o','1':'i','3':'e','4':'a','5':'s','@':'a','$':'s'
})
# Основа слов и распространённые англоязычные ругательства; проверяется нормализованная строка.
RU_STEMS = ('бля','сука','сук','хуй','хуе','хуё','пизд','еба','ёба','ебл','муд','долбо','гандон','залуп','шлюх','чмо','уёб','уеб','нахуй','нахрен')
EN_WORDS = ('fuck','fucking','shit','bitch','asshole','bastard','dickhead','motherfucker','retard')

def normalize(text: str) -> str:
    text = unicodedata.normalize('NFKC', text).lower().translate(LOOKALIKE)
    text = re.sub(r'[^a-z0-9а-я]+', '', text)
    return text

def has_insult(text: str) -> bool:
    value = normalize(text)
    # Нормализуем и список слов тем же способом: иначе кириллица после
    # замены похожих букв не совпадала бы с исходными русскими основами.
    blocked = tuple(normalize(word) for word in RU_STEMS + EN_WORDS)
    return any(word and word in value for word in blocked)

class AutoModerationCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not has_insult(message.content):
            return
        member = message.author
        if not isinstance(member, discord.Member) or not message.guild.me.guild_permissions.moderate_members:
            return
        try:
            await member.timeout(timedelta(minutes=10), reason='Автомод: оскорбление/брань')
            try: await message.delete()
            except discord.HTTPException: pass
            channel = message.guild.get_channel(STAFF_NOTIFY_CHANNEL)
            if channel:
                embed = discord.Embed(title='🔇 Автоматический мут', color=0xE67E22)
                embed.add_field(name='Пользователь', value=f'{member.mention} (`{member.id}`)', inline=False)
                embed.add_field(name='Причина', value='Обнаружена запрещённая брань или оскорбление', inline=False)
                embed.add_field(name='Срок', value='10 минут', inline=True)
                embed.add_field(name='Канал', value=message.channel.mention, inline=True)
                await channel.send(embed=embed)
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass

async def setup(bot): await bot.add_cog(AutoModerationCog(bot))
