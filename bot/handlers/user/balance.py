from __future__ import annotations

import logging

from typing import Callable
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.db import repo
from bot.db.models import OrderKind, OrderStatus, PaymentMethod
from bot.keyboards.user_kb import balance_menu_kb
from bot.payments.base import get_provider
from bot.states.forms import BalanceForm

logger = logging.getLogger(__name__)
router = Router(name="user-balance")


def _fmt(amount: float) -> str:
    return f"{int(amount):,} {get_settings().fiat_currency}"


async def _show_balance(target: Message, user_id: int, _: Callable[[str], str]) -> None:
    balance = await repo.get_balance(user_id)
    can_charge = bool(get_settings().card_number)
    text = _("balance_info", balance=_fmt(balance))
    if can_charge:
        text += _("topup_available")
    else:
        text += _("topup_unavailable")
    await target.answer(text, reply_markup=balance_menu_kb(can_charge, _))


@router.message(F.text.in_(["💰 Balance", "💰 کیف پول"]))
async def balance_entry(message: Message, state: FSMContext, _: Callable[[str], str]) -> None:
    await state.clear()
    await _show_balance(message, message.from_user.id, _)


@router.callback_query(F.data == "bal:history")
async def balance_history(call: CallbackQuery, _: Callable[[str], str]) -> None:
    txns = await repo.list_transactions(call.from_user.id, limit=10)
    if not txns:
        await call.answer(_("no_txns"), show_alert=True)
        return
    lines = [_("recent_txns"), ""]
    for t in txns:
        sign = "➕" if t.amount >= 0 else "➖"
        lines.append(f"{sign} {_fmt(abs(t.amount))} — {t.reason}")
    await call.message.answer("\n".join(lines))
    await call.answer()


@router.callback_query(F.data == "bal:charge")
async def balance_charge(call: CallbackQuery, state: FSMContext, _: Callable[[str], str]) -> None:
    if not get_settings().card_number:
        await call.answer(_("topup_unavailable"), show_alert=True)
        return
    await state.set_state(BalanceForm.amount)
    await call.message.answer(_("how_much_add", currency=get_settings().fiat_currency))
    await call.answer()


@router.message(BalanceForm.amount)
async def balance_amount(message: Message, state: FSMContext, bot: Bot, _: Callable[[str], str]) -> None:
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    try:
        amount = float(raw)
    except ValueError:
        await message.answer(_("invalid_number"))
        return
    if amount <= 0:
        await message.answer(_("greater_than_zero"))
        return

    currency = get_settings().fiat_currency
    order = await repo.create_order(
        user_tg_id=message.from_user.id,
        plan_id=0,  # sentinel: top-ups are not tied to a plan
        method=PaymentMethod.card,
        amount=amount,
        currency=currency,
        status=OrderStatus.pending,
        kind=OrderKind.topup,
    )
    # Reuse the card-to-card receipt flow (plan=None -> "Wallet top-up").
    provider = get_provider(PaymentMethod.card)
    await provider.start_checkout(bot, message.from_user.id, order, None, state)
