"""Интерактивный магазин WeriGrief — PostgreSQL."""

import time
import logging

import discord

from src.core.database import get_pool

VIP_ROLE_ID = 1571419587306586152
VIP_PRICE = 25_000
VIP_DAYS = 30


class VIPConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(
        label="Подтвердить покупку",
        emoji="👑",
        style=discord.ButtonStyle.success
    )
    async def confirm(self, interaction: discord.Interaction, button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Покупка доступна только на сервере.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        role = interaction.guild.get_role(VIP_ROLE_ID)
        if role is None:
            await interaction.followup.send(
                "Не найдена VIP-роль. Сообщи администрации.", ephemeral=True
            )
            return

        bot_member = interaction.guild.me
        if bot_member is None or not bot_member.guild_permissions.manage_roles:
            await interaction.followup.send(
                "Боту нужно право «Управлять ролями».", ephemeral=True
            )
            return

        if role >= bot_member.top_role:
            await interaction.followup.send(
                "Перемести роль бота выше VIP-роли в настройках сервера.",
                ephemeral=True
            )
            return

        user_id = interaction.user.id
        guild_id = interaction.guild.id
        had_role = role in interaction.user.roles
        role_added = False
        new_expiry = None

        try:
            pool = get_pool()

            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Защита от одновременных покупок одного VIP.
                    lock_key = f"vip:{user_id}:{guild_id}"
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                        lock_key
                    )

                    wallet = await conn.fetchrow(
                        """
                        SELECT balance
                        FROM economy.wallets
                        WHERE user_id = $1
                        FOR UPDATE
                        """,
                        user_id
                    )

                    if wallet is None or wallet["balance"] < VIP_PRICE:
                        await interaction.followup.send(
                            f"Недостаточно Риликов. Нужно **{VIP_PRICE:,}**.",
                            ephemeral=True
                        )
                        return

                    old_expiry = await conn.fetchval(
                        """
                        SELECT expires_at
                        FROM store.vip_entitlements
                        WHERE user_id = $1 AND guild_id = $2
                        FOR UPDATE
                        """,
                        user_id,
                        guild_id
                    )

                    new_expiry = max(time.time(), old_expiry or 0) + VIP_DAYS * 86400

                    # Выдаём роль до фиксации покупки.
                    if not had_role:
                        await interaction.user.add_roles(
                            role,
                            reason="Покупка VIP в магазине WeriGrief"
                        )
                        role_added = True

                    await conn.execute(
                        """
                        UPDATE economy.wallets
                        SET balance = balance - $1
                        WHERE user_id = $2
                        """,
                        VIP_PRICE,
                        user_id
                    )

                    await conn.execute(
                        """
                        INSERT INTO economy.transactions(
                            sender_id, receiver_id, amount, reason, created_at
                        )
                        VALUES($1, $1, $2, 'vip_purchase', $3)
                        """,
                        user_id,
                        VIP_PRICE,
                        time.time()
                    )

                    await conn.execute(
                        """
                        INSERT INTO store.vip_entitlements(
                            user_id, guild_id, expires_at
                        )
                        VALUES($1, $2, $3)
                        ON CONFLICT(user_id, guild_id)
                        DO UPDATE SET expires_at = EXCLUDED.expires_at
                        """,
                        user_id,
                        guild_id,
                        new_expiry
                    )

        except discord.Forbidden:
            await interaction.followup.send(
                "Не удалось выдать VIP. Покупка отменена, Рилики не списаны.",
                ephemeral=True
            )
            return

        except Exception:
            logging.exception("Ошибка покупки VIP")
            if role_added:
                try:
                    await interaction.user.remove_roles(
                        role,
                        reason="Отмена неудачной покупки VIP"
                    )
                except Exception:
                    logging.exception("Не удалось отменить выдачу VIP-роли")

            await interaction.followup.send(
                "Покупка не завершена. Проверь баланс и сообщи администрации, "
                "если роль выдалась, но покупка не прошла.",
                ephemeral=True
            )
            return

        self.stop()
        await interaction.followup.send(
            f"👑 VIP активирован на **{VIP_DAYS} дней**!\n"
            f"Списано: **{VIP_PRICE:,} Риликов**.\n"
            f"Дата окончания: <t:{int(new_expiry)}:F>",
            ephemeral=True
        )


class ShopView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(
        label="Купить VIP",
        emoji="👑",
        style=discord.ButtonStyle.success,
        row=0
    )
    async def buy_vip(self, interaction: discord.Interaction, button):
        embed = discord.Embed(
            title="👑 Подтверждение покупки VIP",
            description=(
                f"**Цена:** {VIP_PRICE:,} Риликов\n"
                f"**Срок:** {VIP_DAYS} дней\n\n"
                "После подтверждения Рилики будут списаны, "
                "а VIP-роль выдана автоматически."
            ),
            color=0x9B59B6
        )
        await interaction.response.send_message(
            embed=embed,
            view=VIPConfirmView(),
            ephemeral=True
        )

    @discord.ui.button(
        label="Мой баланс",
        emoji="💎",
        style=discord.ButtonStyle.secondary,
        row=0
    )
    async def balance(self, interaction: discord.Interaction, button):
        pool = get_pool()
        async with pool.acquire() as conn:
            balance = await conn.fetchval(
                """
                SELECT balance
                FROM economy.wallets
                WHERE user_id = $1
                """,
                interaction.user.id
            )

        balance = balance or 0
        await interaction.response.send_message(
            f"💎 Твой баланс: **{balance:,} Риликов**.",
            ephemeral=True
        )

    @discord.ui.button(
        label="Мои покупки",
        emoji="📦",
        style=discord.ButtonStyle.secondary,
        row=0
    )
    async def purchases(self, interaction: discord.Interaction, button):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Эта функция доступна только на сервере.",
                ephemeral=True
            )
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            expiry = await conn.fetchval(
                """
                SELECT expires_at
                FROM store.vip_entitlements
                WHERE user_id = $1 AND guild_id = $2
                """,
                interaction.user.id,
                interaction.guild_id
            )

        if expiry is None or expiry <= time.time():
            message = "У тебя пока нет активного VIP."
        else:
            message = f"👑 VIP действует до <t:{int(expiry)}:F>."

        await interaction.response.send_message(
            message,
            ephemeral=True
        )
