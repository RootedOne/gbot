from __future__ import annotations

from typing import Callable
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import repo
from bot.db.models import Plan
from bot.keyboards.user_kb import plan_detail_kb, plans_kb
from bot.services.pricing import available_methods, plan_caption
from bot.states.forms import CheckoutStates

router = Router(name="user-plans")


async def _show_plans(target: Message, _: Callable[[str], str]) -> None:
    node_id = getattr(target.bot, "node_id", 0)
    plans = await repo.list_plans(only_active=True, node_id=node_id)
    if not plans:
        await target.answer(_("no_plans"))
        return
    await target.answer(_("select_plan"), reply_markup=plans_kb(plans))


@router.message(F.text.in_(["🛒 Buy Plan", "🛒 خرید سرویس"]))
async def buy_plan_entry(message: Message, _: Callable[[str], str]) -> None:
    await _show_plans(message, _)


@router.callback_query(F.data == "plans:list")
async def plans_list_cb(call: CallbackQuery, _: Callable[[str], str]) -> None:
    node_id = getattr(call.bot, "node_id", 0)
    plans = await repo.list_plans(only_active=True, node_id=node_id)
    if not plans:
        await call.message.edit_text(_("no_plans"))
        await call.answer()
        return
    await call.message.edit_text(
        _("select_plan"), reply_markup=plans_kb(plans)
    )
    await call.answer()


@router.callback_query(F.data.startswith("plan:"))
async def plan_detail_cb(call: CallbackQuery, _: Callable[[str], str]) -> None:
    plan_id = int(call.data.split(":", 1)[1])
    plan = await repo.get_plan(plan_id)
    if plan is None or not plan.is_active:
        await call.answer(_("plan_not_found"), show_alert=True)
        return
    methods = available_methods(plan)
    node_id = getattr(call.bot, "node_id", 0)
    balance = await repo.get_balance(call.from_user.id, node_id=node_id)
    can_pay_with_balance = bool(plan.price_fiat) and balance >= float(plan.price_fiat)
    if not methods and not can_pay_with_balance:
        await call.message.edit_text(
            plan_caption(plan)
            + "\n\n⚠️ No payment methods are configured for this plan yet.",
        )
        await call.answer()
        return
    caption = plan_caption(plan)
    if can_pay_with_balance:
        from bot.config import get_settings

        caption += (
            f"\n\n💰 Your balance: {int(balance):,} {get_settings().fiat_currency}"
        )
    await call.message.edit_text(
        caption,
        reply_markup=plan_detail_kb(plan, methods, can_pay_with_balance, _),
    )
    await call.answer()


@router.callback_query(F.data.startswith("plan_bulk:"))
async def plan_bulk_cb(call: CallbackQuery, _: Callable[[str], str]) -> None:
    plan_id = int(call.data.split(":", 1)[1])
    plan = await repo.get_plan(plan_id)
    if plan is None or not plan.is_active:
        await call.answer(_("plan_not_found"), show_alert=True)
        return
    from bot.keyboards.user_kb import bulk_qty_kb
    await call.message.edit_text(
        _("bulk_title", title=plan.title),
        reply_markup=bulk_qty_kb(plan.id, _),
    )
    await call.answer()


@router.callback_query(F.data.startswith("bulk_qty:"))
async def bulk_qty_select_cb(call: CallbackQuery, state: FSMContext, _: Callable[[str], str]) -> None:
    parts = call.data.split(":")
    plan_id = int(parts[1])
    qty_str = parts[2]
    
    plan = await repo.get_plan(plan_id)
    if plan is None or not plan.is_active:
        await call.answer(_("plan_not_found"), show_alert=True)
        return
        
    if qty_str == "custom":
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        await state.set_state(CheckoutStates.awaiting_bulk_qty)
        await state.update_data(plan_id=plan_id)
        await call.message.edit_text(
            _("enter_custom_qty"),
            reply_markup=InlineKeyboardBuilder().button(text=_("btn_back"), callback_data=f"plan_bulk:{plan_id}").as_markup()
        )
        await call.answer()
        return
        
    qty = int(qty_str)
    await _show_bulk_checkout_methods(call, plan, qty, _)


async def _show_bulk_checkout_methods(call: CallbackQuery, plan: Plan, qty: int, _: Callable[[str], str]) -> None:
    methods = available_methods(plan)
    node_id = getattr(call.bot, "node_id", 0)
    balance = await repo.get_balance(call.from_user.id, node_id=node_id)
    total_fiat_price = float(plan.price_fiat or 0) * qty
    can_pay_with_balance = bool(plan.price_fiat) and balance >= total_fiat_price
    
    caption = _("bulk_checkout_title", title=plan.title, qty=qty)
    from bot.config import get_settings
    s = get_settings()
    if plan.price_fiat:
        caption += f"\n💳 Price: {int(total_fiat_price):,} {s.fiat_currency} (for {qty}x)"
    if plan.price_stars:
        total_stars = plan.price_stars * qty
        caption += f"\n⭐ Price: {total_stars:,} Stars (for {qty}x)"
    if plan.price_usd:
        total_usd = plan.price_usd * qty
        caption += f"\n🪙 Price: {total_usd:,} USD (for {qty}x)"
        
    if can_pay_with_balance:
        caption += f"\n\n💰 Your balance: {int(balance):,} {s.fiat_currency}"
        
    from bot.keyboards.user_kb import bulk_payment_kb
    await call.message.edit_text(
        caption,
        reply_markup=bulk_payment_kb(plan.id, qty, methods, can_pay_with_balance, _),
    )
    await call.answer()


@router.message(CheckoutStates.awaiting_bulk_qty, F.text)
async def custom_bulk_qty_handler(message: Message, state: FSMContext, _: Callable[[str], str]) -> None:
    data = await state.get_data()
    plan_id = data.get("plan_id")
    if plan_id is None:
        await state.clear()
        await message.answer(_("no_active_order"))
        return
        
    try:
        qty = int(message.text)
        if qty < 1 or qty > 100:
            raise ValueError
    except ValueError:
        await message.answer(_("invalid_qty"))
        return
        
    await state.clear()
    plan = await repo.get_plan(plan_id)
    if plan is None or not plan.is_active:
        await message.answer(_("plan_not_found"))
        return
        
    await _show_bulk_checkout_methods_new_message(message, plan, qty, _)


async def _show_bulk_checkout_methods_new_message(message: Message, plan: Plan, qty: int, _: Callable[[str], str]) -> None:
    methods = available_methods(plan)
    node_id = getattr(message.bot, "node_id", 0)
    balance = await repo.get_balance(message.from_user.id, node_id=node_id)
    total_fiat_price = float(plan.price_fiat or 0) * qty
    can_pay_with_balance = bool(plan.price_fiat) and balance >= total_fiat_price
    
    caption = _("bulk_checkout_title", title=plan.title, qty=qty)
    from bot.config import get_settings
    s = get_settings()
    if plan.price_fiat:
        caption += f"\n💳 Price: {int(total_fiat_price):,} {s.fiat_currency} (for {qty}x)"
    if plan.price_stars:
        total_stars = plan.price_stars * qty
        caption += f"\n⭐ Price: {total_stars:,} Stars (for {qty}x)"
    if plan.price_usd:
        total_usd = plan.price_usd * qty
        caption += f"\n🪙 Price: {total_usd:,} USD (for {qty}x)"
        
    if can_pay_with_balance:
        caption += f"\n\n💰 Your balance: {int(balance):,} {s.fiat_currency}"
        
    from bot.keyboards.user_kb import bulk_payment_kb
    await message.answer(
        caption,
        reply_markup=bulk_payment_kb(plan.id, qty, methods, can_pay_with_balance, _),
    )
