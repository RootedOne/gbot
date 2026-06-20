from __future__ import annotations

import logging

from typing import Callable
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery

from bot.config import get_settings
from bot.db import repo
from bot.db.models import OrderStatus, PaymentMethod
from bot.payments.base import get_provider
from bot.services.fulfillment import fulfill_order
from bot.services.pricing import adjust_plan_for_reseller, amount_for, adjust_plan_for_promo
from bot.states.forms import CheckoutStates
import time

logger = logging.getLogger(__name__)
router = Router(name="user-checkout")


@router.callback_query(F.data.startswith("buy:"))
async def buy_cb(call: CallbackQuery, state: FSMContext, bot: Bot, _: Callable[[str], str]) -> None:
    _prefix, plan_id_raw, method_raw = call.data.split(":")
    plan = await repo.get_plan(int(plan_id_raw))
    if plan is None or not plan.is_active:
        await call.answer(_("plan_not_found"), show_alert=True)
        return
    node_id = getattr(bot, "node_id", 0)
    await adjust_plan_for_reseller(plan, call.from_user.id, node_id)
    try:
        method = PaymentMethod(method_raw)
    except ValueError:
        await call.answer(_("unknown_method"), show_alert=True)
        return

    state_data = await state.get_data()
    applied_promo = state_data.get("applied_promo_code")
    discount_amount = 0.0
    promo_code_str = None
    if applied_promo:
        promo = await repo.get_promo_code_by_code(applied_promo, node_id=node_id)
        if promo and promo.is_active:
            if promo.expiry_time is None or promo.expiry_time > int(time.time() * 1000):
                if promo.max_uses is None or promo.used_count < promo.max_uses:
                    if not await repo.has_user_used_promo(call.from_user.id, promo.code, node_id=node_id):
                        discount_amount = adjust_plan_for_promo(plan, promo)
                        promo_code_str = promo.code

    if method == PaymentMethod.wallet:
        await _pay_with_balance(call, bot, plan, promo_code_str, discount_amount, _)
        await state.update_data(applied_promo_code=None)
        return

    amount, currency = amount_for(plan, method)
    if amount <= 0:
        await call.answer(_("method_unavailable"), show_alert=True)
        return

    node_id = getattr(bot, "node_id", 0)
    order = await repo.create_order(
        user_tg_id=call.from_user.id,
        plan_id=plan.id,
        method=method,
        amount=amount,
        currency=currency,
        status=OrderStatus.pending,
        node_id=node_id,
        promo_code=promo_code_str,
        discount_amount=discount_amount,
    )

    await state.update_data(applied_promo_code=None)
    provider = get_provider(method)
    await call.message.answer(_("preparing_order"))
    await provider.start_checkout(bot, call.from_user.id, order, plan, state)
    await call.answer()


@router.callback_query(F.data.startswith("bulk_buy:"))
async def bulk_buy_cb(call: CallbackQuery, state: FSMContext, bot: Bot, _: Callable[[str], str]) -> None:
    _prefix, plan_id_raw, qty_raw, method_raw = call.data.split(":")
    plan = await repo.get_plan(int(plan_id_raw))
    if plan is None or not plan.is_active:
        await call.answer(_("plan_not_found"), show_alert=True)
        return
    node_id = getattr(bot, "node_id", 0)
    await adjust_plan_for_reseller(plan, call.from_user.id, node_id)
    try:
        qty = int(qty_raw)
        if qty < 1 or qty > 100:
            raise ValueError
    except ValueError:
        await call.answer(_("invalid_qty"), show_alert=True)
        return
    try:
        method = PaymentMethod(method_raw)
    except ValueError:
        await call.answer(_("unknown_method"), show_alert=True)
        return

    state_data = await state.get_data()
    applied_promo = state_data.get("applied_promo_code")
    discount_amount = 0.0
    promo_code_str = None
    if applied_promo:
        promo = await repo.get_promo_code_by_code(applied_promo, node_id=node_id)
        if promo and promo.is_active:
            if promo.expiry_time is None or promo.expiry_time > int(time.time() * 1000):
                if promo.max_uses is None or promo.used_count < promo.max_uses:
                    if not await repo.has_user_used_promo(call.from_user.id, promo.code, node_id=node_id):
                        discount_amount = adjust_plan_for_promo(plan, promo) * qty
                        promo_code_str = promo.code

    if method == PaymentMethod.wallet:
        await _bulk_pay_with_balance(call, bot, plan, qty, promo_code_str, discount_amount, _)
        await state.update_data(applied_promo_code=None)
        return

    base_amount, currency = amount_for(plan, method)
    if base_amount <= 0:
        await call.answer(_("method_unavailable"), show_alert=True)
        return
    amount = base_amount * qty

    node_id = getattr(bot, "node_id", 0)
    order = await repo.create_order(
        user_tg_id=call.from_user.id,
        plan_id=plan.id,
        method=method,
        amount=amount,
        currency=currency,
        status=OrderStatus.pending,
        node_id=node_id,
        quantity=qty,
        promo_code=promo_code_str,
        discount_amount=discount_amount,
    )

    await state.update_data(applied_promo_code=None)
    provider = get_provider(method)
    await call.message.answer(_("preparing_order"))
    await provider.start_checkout(bot, call.from_user.id, order, plan, state)
    await call.answer()


async def _bulk_pay_with_balance(
    call: CallbackQuery,
    bot: Bot,
    plan,
    qty: int,
    promo_code: Optional[str],
    discount_amount: float,
    _: Callable[[str], str]
) -> None:
    from bot.config import get_settings
    from bot.db.models import OrderKind
    from bot.db.repo import InsufficientBalance

    price = float(plan.price_fiat or 0) * qty
    currency = get_settings().fiat_currency
    if price <= 0:
        await call.answer(_("cant_pay_balance"), show_alert=True)
        return
    node_id = getattr(bot, "node_id", 0)
    balance = await repo.get_balance(call.from_user.id, node_id=node_id)
    if balance < price:
        await call.answer(
            _("insufficient_balance", current=int(balance), price=int(price), currency=currency),
            show_alert=True,
        )
        return

    try:
        await repo.adjust_balance(
            call.from_user.id, -price, reason=f"Purchase: {qty}x {plan.title}", node_id=node_id
        )
    except InsufficientBalance:
        await call.answer(_("insufficient_balance_short"), show_alert=True)
        return

    order = await repo.create_order(
        user_tg_id=call.from_user.id,
        plan_id=plan.id,
        method=PaymentMethod.wallet,
        amount=price,
        currency=currency,
        status=OrderStatus.pending,
        kind=OrderKind.plan,
        node_id=node_id,
        quantity=qty,
        promo_code=promo_code,
        discount_amount=discount_amount,
    )
    await call.message.answer(_("paid_from_balance"))
    ok = await fulfill_order(bot, order)
    if not ok:
        # refund on provisioning failure
        await repo.adjust_balance(
            call.from_user.id, price, reason=f"Refund: {qty}x {plan.title} (order #{order.id})", node_id=node_id
        )
        await call.message.answer(_("provision_failed_refund"))
    await call.answer()


async def _pay_with_balance(
    call: CallbackQuery,
    bot: Bot,
    plan,
    promo_code: Optional[str],
    discount_amount: float,
    _: Callable[[str], str]
) -> None:
    from bot.config import get_settings
    from bot.db.models import OrderKind
    from bot.db.repo import InsufficientBalance

    price = float(plan.price_fiat or 0)
    currency = get_settings().fiat_currency
    if price <= 0:
        await call.answer(_("cant_pay_balance"), show_alert=True)
        return
    node_id = getattr(bot, "node_id", 0)
    balance = await repo.get_balance(call.from_user.id, node_id=node_id)
    if balance < price:
        await call.answer(
            _("insufficient_balance", current=int(balance), price=int(price), currency=currency),
            show_alert=True,
        )
        return

    try:
        await repo.adjust_balance(
            call.from_user.id, -price, reason=f"Purchase: {plan.title}", node_id=node_id
        )
    except InsufficientBalance:
        await call.answer(_("insufficient_balance_short"), show_alert=True)
        return

    order = await repo.create_order(
        user_tg_id=call.from_user.id,
        plan_id=plan.id,
        method=PaymentMethod.wallet,
        amount=price,
        currency=currency,
        status=OrderStatus.pending,
        kind=OrderKind.plan,
        node_id=node_id,
        promo_code=promo_code,
        discount_amount=discount_amount,
    )
    await call.message.answer(_("paid_from_balance"))
    ok = await fulfill_order(bot, order)
    if not ok:
        # refund on provisioning failure
        await repo.adjust_balance(
            call.from_user.id, price, reason=f"Refund: {plan.title} (order #{order.id})", node_id=node_id
        )
        await call.message.answer(_("provision_failed_refund"))
    await call.answer()


# ----------------------- card-to-card receipt -----------------------

@router.message(CheckoutStates.awaiting_receipt, F.photo)
async def receipt_photo(message: Message, state: FSMContext, bot: Bot, _: Callable[[str], str]) -> None:
    data = await state.get_data()
    order_id = data.get("order_id")
    await state.clear()
    if not order_id:
        await message.answer(_("no_active_order"))
        return

    file_id = message.photo[-1].file_id
    order = await repo.set_order_status(
        order_id, OrderStatus.awaiting_review, receipt_file_id=file_id
    )
    if order is None:
        await message.answer(_("order_not_found"))
        return

    await message.answer(_("receipt_received"))
    await _notify_admins_receipt(bot, order_id, message.from_user.id, file_id)


@router.message(CheckoutStates.awaiting_receipt)
async def receipt_not_photo(message: Message, _: Callable[[str], str]) -> None:
    await message.answer(_("send_receipt_photo"))


async def _notify_admins_receipt(
    bot: Bot, order_id: int, user_id: int, file_id: str
) -> None:
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    from bot.db.models import OrderKind

    order = await repo.get_order(order_id)
    amount_line = (
        f"Amount: {int(order.amount):,} {order.currency}" if order else "Amount: ?"
    )
    if order and order.kind == OrderKind.topup.value:
        what = "💰 Wallet top-up"
    elif order and order.kind == OrderKind.upgrade.value:
        plan = await repo.get_plan(order.plan_id) if order else None
        what = f"Upgrade to: {plan.title if plan else '?'}"
    else:
        plan = await repo.get_plan(order.plan_id) if order else None
        qty = getattr(order, "quantity", 1) or 1
        qty_str = f" ({qty}x)" if qty > 1 else ""
        what = f"Plan: {plan.title if plan else '?'}{qty_str}"
    promo_line = ""
    if order and order.promo_code:
        promo_line = f"\n🎟 Promo Code: <b>{order.promo_code}</b> (-{int(order.discount_amount):,} {order.currency})"

    caption = (
        f"🧾 <b>New receipt — order #{order_id}</b>\n"
        f"User: <code>{user_id}</code>\n"
        f"{what}\n"
        f"{amount_line}"
        f"{promo_line}"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Approve", callback_data=f"adm:order:approve:{order_id}")
    kb.button(text="❌ Reject", callback_data=f"adm:order:reject:{order_id}")
    kb.adjust(2)

    node_id = getattr(bot, "node_id", 0)
    if node_id == 0:
        admins = get_settings().admin_ids
    else:
        node = await repo.get_node(node_id)
        admins = [node.owner_tg_id] if node else []

    for admin_id in admins:
        try:
            await bot.send_photo(
                admin_id, file_id, caption=caption, reply_markup=kb.as_markup()
            )
        except Exception:  # noqa: BLE001
            continue


# --------------------------- Telegram Stars ---------------------------

@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, bot: Bot) -> None:
    # Approve all pre-checkouts; the order already exists in our DB.
    await bot.answer_pre_checkout_query(query.id, ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, bot: Bot, _: Callable[[str], str]) -> None:
    payload = message.successful_payment.invoice_payload or ""
    if not payload.startswith("order:"):
        return
    try:
        order_id = int(payload.split(":", 1)[1])
    except (ValueError, IndexError):
        return
    order = await repo.get_order(order_id)
    if order is None:
        await message.answer(_("order_not_found_payment"))
        return
    charge_id = message.successful_payment.telegram_payment_charge_id
    await repo.set_order_status(order_id, order.status, provider_ref=charge_id)
    await message.answer(_("payment_confirmed"))
    await fulfill_order(bot, order)


@router.callback_query(F.data.startswith("copy:card:"))
async def copy_card_cb(call: CallbackQuery, _: Callable[[str], str]) -> None:
    s = get_settings()
    card = s.card_number.replace("-", "").replace(" ", "")
    await call.message.answer(f"<code>{card}</code>")
    await call.answer(_("copy_card_toast"))


@router.callback_query(F.data.startswith("copy:amount:"))
async def copy_amount_cb(call: CallbackQuery, _: Callable[[str], str]) -> None:
    parts = call.data.split(":")
    if len(parts) < 3:
        await call.answer("Invalid callback data", show_alert=True)
        return
    try:
        order_id = int(parts[2])
    except ValueError:
        await call.answer("Invalid order ID", show_alert=True)
        return
    order = await repo.get_order(order_id)
    if order:
        amount = str(int(order.amount))
        await call.message.answer(f"<code>{amount}</code>")
        await call.answer(_("copy_amount_toast"))
    else:
        await call.answer(_("order_not_found"), show_alert=True)

