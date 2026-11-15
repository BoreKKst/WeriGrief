import discord


class MemberListView(discord.ui.View):
    """Постраничный список участников сервера."""

    def __init__(self, members, owner_id: int):
        super().__init__(timeout=180)
        self.members = members
        self.owner_id = owner_id
        self.page = 0
        self.page_size = 15

        self.previous_button = discord.ui.Button(
            label="Назад",
            emoji="◀️",
            style=discord.ButtonStyle.secondary
        )
        self.next_button = discord.ui.Button(
            label="Вперёд",
            emoji="▶️",
            style=discord.ButtonStyle.secondary
        )

        self.previous_button.callback = self.previous_page
        self.next_button.callback = self.next_page

        self.add_item(self.previous_button)
        self.add_item(self.next_button)
        self.update_buttons()

    @property
    def page_count(self):
        return max(1, (len(self.members) + self.page_size - 1) // self.page_size)

    def update_buttons(self):
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.page_count - 1

    def make_embed(self):
        start = self.page * self.page_size
        current = self.members[start:start + self.page_size]

        lines = []
        for index, member in enumerate(current, start=start + 1):
            kind = "🤖" if member.bot else "👤"
            role = member.top_role
            role_text = (
                f" · {role.mention}"
                if role and role != member.guild.default_role
                else ""
            )
            lines.append(
                f"`{index:03}` {kind} {member.mention}{role_text}"
            )

        embed = discord.Embed(
            title="🌙 WeriGrief • Участники сервера",
            description="\n".join(lines) or "Участников нет.",
            color=0x9B59B6
        )
        embed.add_field(
            name="👥 Всего участников",
            value=f"**{len(self.members):,}**",
            inline=True
        )
        embed.add_field(
            name="📄 Страница",
            value=f"**{self.page + 1}/{self.page_count}**",
            inline=True
        )
        embed.set_footer(text="WeriGrief • Список участников")
        return embed

    async def change_page(self, interaction, delta):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Это меню открыл другой участник.",
                ephemeral=True
            )
            return

        self.page = max(
            0,
            min(self.page + delta, self.page_count - 1)
        )
        self.update_buttons()
        await interaction.response.edit_message(
            embed=self.make_embed(),
            view=self
        )

    async def previous_page(self, interaction):
        await self.change_page(interaction, -1)

    async def next_page(self, interaction):
        await self.change_page(interaction, 1)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
