import discord

from src.modules.pets.database import PETS, FOOD


class RenamePetModal(discord.ui.Modal, title="Переименование питомца"):
    new_name = discord.ui.TextInput(
        label="Новое имя",
        placeholder="Например, Лунтик",
        min_length=2,
        max_length=32,
    )

    def __init__(self, cog, pet_id):
        super().__init__()
        self.cog = cog
        self.pet_id = pet_id

    async def on_submit(self, interaction):
        ok = await self.cog.db.rename(
            self.pet_id,
            interaction.user.id,
            str(self.new_name),
        )

        if not ok:
            await interaction.response.send_message(
                "Не удалось переименовать питомца.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "✏️ Имя питомца изменено!",
            ephemeral=True,
        )


class CoOwnerModal(discord.ui.Modal, title="Второй хозяин"):
    member_id = discord.ui.TextInput(
        label="ID участника Discord",
        placeholder="Вставь Discord ID участника",
        min_length=17,
        max_length=20,
    )

    def __init__(self, cog, pet_id, owner_id):
        super().__init__()
        self.cog = cog
        self.pet_id = pet_id
        self.owner_id = owner_id

    async def on_submit(self, interaction):
        pet = await self.cog.db.get_pet(self.pet_id)

        if not pet or pet["owner_id"] != interaction.user.id:
            await interaction.response.send_message(
                "Добавить второго хозяина может только основной владелец.",
                ephemeral=True,
            )
            return

        try:
            target_id = int(str(self.member_id).strip())
        except ValueError:
            await interaction.response.send_message(
                "Укажи корректный Discord ID.",
                ephemeral=True,
            )
            return

        if target_id == interaction.user.id:
            await interaction.response.send_message(
                "Ты уже основной хозяин.",
                ephemeral=True,
            )
            return

        try:
            member = await interaction.guild.fetch_member(target_id)
        except (discord.NotFound, discord.HTTPException):
            await interaction.response.send_message(
                "Участник не найден на этом сервере.",
                ephemeral=True,
            )
            return

        if member.bot:
            await interaction.response.send_message(
                "Бот не может быть вторым хозяином.",
                ephemeral=True,
            )
            return

        ok = await self.cog.db.set_coowner(
            self.pet_id,
            interaction.user.id,
            member.id,
        )

        if not ok:
            await interaction.response.send_message(
                "Не удалось назначить второго хозяина.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🤝 {member.mention} теперь второй хозяин питомца!",
            ephemeral=True,
        )


class PetSelect(discord.ui.Select):
    def __init__(self, view):
        self.parent_view = view
        options = []

        for pet in view.pets[:25]:
            species = PETS[pet["species"]]
            options.append(discord.SelectOption(
                label=f"{pet['name']} • ур. {pet['level']}"[:100],
                value=str(pet["id"]),
                emoji=species["emoji"],
                default=pet["id"] == view.selected_id,
            ))

        super().__init__(
            placeholder="Выбери своего питомца",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        if interaction.user.id != self.parent_view.user_id:
            await interaction.response.send_message(
                "Это меню открыл другой участник.",
                ephemeral=True,
            )
            return

        self.parent_view.selected_id = int(self.values[0])
        self.parent_view.update_select()

        pet = await self.parent_view.cog.db.refresh(
            self.parent_view.selected_id
        )

        await interaction.response.edit_message(
            embed=self.parent_view.cog.make_pet_embed(pet),
            view=self.parent_view,
        )


class PetView(discord.ui.View):
    def __init__(self, cog, user_id, pets, selected_id=None):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.pets = pets
        self.selected_id = selected_id or pets[0]["id"]
        self.add_item(PetSelect(self))

    def update_select(self):
        self.clear_items()
        self.add_item(PetSelect(self))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Открой `/pets`, чтобы управлять своими питомцами.",
                ephemeral=True,
            )
            return False
        return True

    async def selected_pet(self):
        return await self.cog.db.get_pet(self.selected_id)

    @discord.ui.button(
        label="Покормить",
        emoji="🍖",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def feed_button(self, interaction, button):
        ok, message = await self.cog.db.feed(
            self.selected_id,
            interaction.user.id,
        )
        pet = await self.cog.db.refresh(self.selected_id)

        await interaction.response.edit_message(
            content=message,
            embed=self.cog.make_pet_embed(pet),
            view=self,
        )

    @discord.ui.button(
        label="Играть",
        emoji="🎾",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def play_button(self, interaction, button):
        ok, message = await self.cog.db.play(
            self.selected_id,
            interaction.user.id,
        )
        pet = await self.cog.db.refresh(self.selected_id)

        await interaction.response.edit_message(
            content=message,
            embed=self.cog.make_pet_embed(pet),
            view=self,
        )

    @discord.ui.button(
        label="Имя",
        emoji="✏️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def rename_button(self, interaction, button):
        await interaction.response.send_modal(
            RenamePetModal(self.cog, self.selected_id)
        )

    @discord.ui.button(
        label="Второй хозяин",
        emoji="🤝",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def coowner_button(self, interaction, button):
        pet = await self.selected_pet()

        if not pet or pet["owner_id"] != interaction.user.id:
            await interaction.response.send_message(
                "Добавить второго хозяина может только основной владелец.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            CoOwnerModal(self.cog, self.selected_id, interaction.user.id)
        )


class PetShopSelect(discord.ui.Select):
    def __init__(self, cog):
        self.cog = cog
        options = []

        for key, pet in PETS.items():
            options.append(discord.SelectOption(
                label=pet["name"],
                description=f"{pet['price']:,} Риликов",
                value="pet:" + key,
                emoji=pet["emoji"],
            ))

        for key, food in FOOD.items():
            options.append(discord.SelectOption(
                label=food["name"],
                description=f"{food['price']:,} Риликов",
                value="food:" + key,
                emoji=food["emoji"],
            ))

        super().__init__(
            placeholder="Выбери питомца или корм",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        kind, key = self.values[0].split(":", 1)

        if kind == "pet":
            pet = PETS[key]
            ok, message = await self.cog.db.purchase_pet(
                interaction.user.id,
                key,
                pet["name"],
            )
        else:
            ok, message = await self.cog.db.purchase_food(
                interaction.user.id,
                key,
            )

        balance = await self.cog.db.balance(interaction.user.id)

        await interaction.response.send_message(
            f"{'✅' if ok else '❌'} {message}\n"
            f"💰 Баланс: **{balance:,} Риликов**",
            ephemeral=True,
        )


class PetShopView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=180)
        self.add_item(PetShopSelect(cog))
