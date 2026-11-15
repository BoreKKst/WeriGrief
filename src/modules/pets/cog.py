import discord
from discord.ext import commands

from src.modules.pets.database import PetsDatabase, PETS, FOOD
from src.modules.pets.ui import PetView, PetShopView


class PetsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = PetsDatabase()

    @staticmethod
    def make_pet_embed(pet):
        if not pet:
            return discord.Embed(
                title="🐾 WeriPets",
                description="Питомец не найден.",
                color=0x9B59B6,
            )

        info = PETS[pet["species"]]
        xp_needed = pet["level"] * 100

        embed = discord.Embed(
            title=f"{info['emoji']} {pet['name']}",
            description=(
                f"{info['description']}\n\n"
                f"⭐ **Уровень:** {pet['level']}\n"
                f"✨ **Опыт:** {pet['xp']}/{xp_needed}\n"
                f"🍖 **Сытость:** {pet['hunger']}/100\n"
                f"❤️ **Настроение:** {pet['mood']}/100"
            ),
            color=0x9B59B6,
        )

        embed.add_field(
            name="🍖 Сытость",
            value=f"`{'█' * (pet['hunger'] // 10)}{'░' * (10 - pet['hunger'] // 10)}`",
            inline=False,
        )
        embed.add_field(
            name="😊 Настроение",
            value=f"`{'█' * (pet['mood'] // 10)}{'░' * (10 - pet['mood'] // 10)}`",
            inline=False,
        )

        owner = f"<@{pet['owner_id']}>"
        co_owner = (
            f"<@{pet['co_owner_id']}>" if pet["co_owner_id"] else "Не назначен"
        )

        embed.add_field(name="👑 Хозяин", value=owner, inline=True)
        embed.add_field(name="🤝 Второй хозяин", value=co_owner, inline=True)
        embed.set_footer(text=f"WeriGrief • WeriPets • ID: {pet['id']}")
        return embed

    @commands.hybrid_command(
        name="pets",
        description="Открыть меню своих питомцев",
    )
    async def pets(self, ctx):
        pets = await self.db.get_pets(ctx.author.id)

        if not pets:
            embed = discord.Embed(
                title="🐾 У тебя пока нет питомцев",
                description=(
                    "Загляни в `/petshop`, чтобы выбрать первого друга!\n\n"
                    "Максимум — 5 питомцев."
                ),
                color=0x9B59B6,
            )
            await ctx.send(embed=embed)
            return

        pet = await self.db.refresh(pets[0]["id"])
        view = PetView(self, ctx.author.id, pets, selected_id=pet["id"])
        await ctx.send(embed=self.make_pet_embed(pet), view=view)

    @commands.hybrid_command(
        name="petshop",
        description="Магазин питомцев и еды",
    )
    async def petshop(self, ctx):
        balance = await self.db.balance(ctx.author.id)

        embed = discord.Embed(
            title="🌙 WeriPets • Магазин",
            description=(
                "Выбери питомца или корм в меню ниже.\n\n"
                "**🐱 Лунный кот** — 2 500 Риликов\n"
                "**🦊 Аметистовый лис** — 5 000 Риликов\n"
                "**🐉 Кристальный дракон** — 15 000 Риликов\n\n"
                "**🍖 Вкусняшка** — 250 Риликов\n"
                "**🌙 Лунный корм** — 600 Риликов"
            ),
            color=0x9B59B6,
        )
        embed.add_field(
            name="💰 Твой баланс",
            value=f"**{balance:,} Риликов**",
            inline=False,
        )
        embed.set_footer(text="WeriGrief • WeriPets")
        await ctx.send(embed=embed, view=PetShopView(self))


async def setup(bot):
    await bot.add_cog(PetsCog(bot))
