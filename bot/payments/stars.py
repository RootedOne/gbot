from __future__ import annotations

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import LabeledPrice

from bot.db.models import Order, PaymentMethod, Plan
from bot.payments.base import PaymentProvider


class StarsProvider(PaymentProvider):
    """Telegram Stars native payment (currency XTR)."""

    method = PaymentMethod.stars

    async def start_checkout(
        self,
        bot: Bot,
        chat_id: int,
        order: Order,
        plan: Plan,
        state: FSMContext,
    ) -> None:
        amount = int(order.amount)
        await bot.send_invoice(
            chat_id=chat_id,
            title=plan.title[:32] or "VPN Plan",
            description=(plan.description or "VPN subscription")[:255],
            # payload carries the order id so successful_payment can fulfill it.
            payload=f"order:{order.id}",
            currency="XTR",
            prices=[LabeledPrice(label=plan.title[:32] or "VPN Plan", amount=amount)],
            # Stars invoices use an empty provider_token.
            provider_token="",
        )
