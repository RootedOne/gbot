from __future__ import annotations

import logging

import httpx
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import get_settings
from bot.db import repo
from bot.db.models import Order, PaymentMethod, Plan
from bot.payments.base import PaymentProvider

logger = logging.getLogger(__name__)

NOWPAYMENTS_API = "https://api.nowpayments.io/v1"


class CryptoProvider(PaymentProvider):
    """Crypto payments via NowPayments hosted invoice + IPN callback."""

    method = PaymentMethod.crypto

    async def start_checkout(
        self,
        bot: Bot,
        chat_id: int,
        order: Order,
        plan: Plan,
        state: FSMContext,
    ) -> None:
        settings = get_settings()
        if not settings.crypto_enabled or not settings.nowpayments_api_key:
            await bot.send_message(
                chat_id, "⚠️ Crypto payments are currently unavailable."
            )
            return

        ipn_url = ""
        if settings.public_base_url:
            ipn_url = f"{settings.public_base_url}/nowpayments/ipn"

        payload = {
            "price_amount": float(order.amount),
            "price_currency": "usd",
            "order_id": str(order.id),
            "order_description": f"{plan.title} (order #{order.id})",
            "ipn_callback_url": ipn_url,
            "is_fixed_rate": True,
        }
        headers = {"x-api-key": settings.nowpayments_api_key}

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(
                    f"{NOWPAYMENTS_API}/invoice", json=payload, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.exception("NowPayments invoice failed: %s", exc)
            await bot.send_message(
                chat_id, "⚠️ Could not create a crypto invoice. Try again later."
            )
            return

        invoice_url = data.get("invoice_url")
        invoice_id = str(data.get("id", ""))
        await repo.set_order_status(order.id, order.status, provider_ref=invoice_id)

        if not invoice_url:
            await bot.send_message(
                chat_id, "⚠️ Crypto invoice missing payment URL. Contact support."
            )
            return

        kb = InlineKeyboardBuilder()
        kb.button(text="💳 Pay with crypto", url=invoice_url)
        await bot.send_message(
            chat_id,
            f"🪙 <b>Order #{order.id}</b> — {plan.title}\n\n"
            f"Amount: <b>${order.amount:g}</b>\n\n"
            "Tap the button to pay. Your service activates automatically once the "
            "payment is confirmed on-chain.",
            reply_markup=kb.as_markup(),
        )
