from __future__ import annotations

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import get_settings
from bot.db import repo
from bot.db.models import Order, PaymentMethod, Plan
from bot.payments.base import PaymentProvider
from bot.states.forms import CheckoutStates
from bot.utils.locales import get_text


class ManualReceiptProvider(PaymentProvider):
    """Card-to-card: show card details, collect a receipt photo for admin review."""

    method = PaymentMethod.card

    async def start_checkout(
        self,
        bot: Bot,
        chat_id: int,
        order: Order,
        plan: Plan,
        state: FSMContext,
    ) -> None:
        settings = get_settings()
        db_user = await repo.get_user(chat_id)
        lang = db_user.lang if db_user else "en"
        _ = lambda k: get_text(lang, k)

        what = plan.title if plan is not None else "Wallet top-up"
        tail = (
            "Your service is activated automatically once an admin approves it."
            if plan is not None
            else "Your balance is credited automatically once an admin approves it."
        )
        promo_line = ""
        if order.promo_code:
            promo_line = f"🎟 <b>Promo Code:</b> {order.promo_code} applied (-{int(order.discount_amount):,} {order.currency})\n\n"

        text = (
            f"🧾 <b>Order #{order.id}</b> — {what}\n\n"
            f"{promo_line}"
            f"💳 Please transfer <b>{int(order.amount):,} {order.currency}</b> to:\n\n"
            f"<b>Card:</b> <code>{settings.card_number}</code>\n"
            f"<b>Holder:</b> {settings.card_holder}\n\n"
            "After paying, <b>send a photo of your receipt</b> in this chat. "
            + tail
        )

        kb = InlineKeyboardBuilder()
        kb.button(text=_("btn_copy_card"), callback_data=f"copy:card:{order.id}")
        kb.button(text=_("btn_copy_amount"), callback_data=f"copy:amount:{order.id}")
        kb.adjust(2)

        await state.set_state(CheckoutStates.awaiting_receipt)
        await state.update_data(order_id=order.id)
        await bot.send_message(chat_id, text, reply_markup=kb.as_markup())

