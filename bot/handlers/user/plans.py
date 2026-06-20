from __future__ import annotations

from typing import Callable
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import repo
from bot.db.models import Plan
from bot.keyboards.user_kb import plan_detail_kb, plans_kb
import time
from bot.services.pricing import adjust_plan_for_reseller, available_methods, plan_caption, adjust_plan_for_promo
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
async def plans_list_cb(call: CallbackQuery, state: FSMContext, _: Callable[[str], str]) -> None:
    await state.clear()
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


async def _show_plan_details(target: Message | CallbackQuery, plan_id: int, state: FSMContext, _: Callable[[str], str]) -> None:
    plan = await repo.get_plan(plan_id)
    if plan is None or not plan.is_active:
        if isinstance(target, CallbackQuery):
            await target.answer(_("plan_not_found"), show_alert=True)
        else:
            await target.answer(_("plan_not_found"))
        return
        
    bot = target.bot
    node_id = getattr(bot, "node_id", 0)
    user_id = target.from_user.id
    
    await adjust_plan_for_reseller(plan, user_id, node_id)
    
    state_data = await state.get_data()
    applied_promo = state_data.get("applied_promo_code")
    discount_amount = 0.0
    promo_code_str = None
    
    if applied_promo:
        promo = await repo.get_promo_code_by_code(applied_promo, node_id=node_id)
        if promo and promo.is_active:
            if promo.expiry_time is None or promo.expiry_time > int(time.time() * 1000):
                if promo.max_uses is None or promo.used_count < promo.max_uses:
                    if not await repo.has_user_used_promo(user_id, promo.code, node_id=node_id):
                        discount_amount = adjust_plan_for_promo(plan, promo)
                        promo_code_str = promo.code
                    else:
                        await state.update_data(applied_promo_code=None)
                else:
                    await state.update_data(applied_promo_code=None)
            else:
                await state.update_data(applied_promo_code=None)
        else:
            await state.update_data(applied_promo_code=None)
            
    methods = available_methods(plan)
    balance = await repo.get_balance(user_id, node_id=node_id)
    can_pay_with_balance = bool(plan.price_fiat) and balance >= float(plan.price_fiat)
    
    caption = plan_caption(plan)
    if promo_code_str:
        from bot.config import get_settings
        cur = get_settings().fiat_currency
        caption += _("promo_discount_line", code=promo_code_str, discount=f"{int(discount_amount):,} {cur}")
        
    if can_pay_with_balance:
        from bot.config import get_settings
        caption += (
            f"\n\n💰 Your balance: {int(balance):,} {get_settings().fiat_currency}"
        )
        
    kb = plan_detail_kb(plan, methods, can_pay_with_balance, promo_code_str, _)
    
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(caption, reply_markup=kb)
        await target.answer()
    else:
        await target.answer(caption, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^plan:\d+$"))
async def plan_detail_cb(call: CallbackQuery, state: FSMContext, _: Callable[[str], str]) -> None:
    plan_id = int(call.data.split(":", 1)[1])
    await _show_plan_details(call, plan_id, state, _)


@router.callback_query(F.data.startswith("plan_bulk:"))
async def plan_bulk_cb(call: CallbackQuery, _: Callable[[str], str]) -> None:
    plan_id = int(call.data.split(":", 1)[1])
    plan = await repo.get_plan(plan_id)
    if plan is None or not plan.is_active:
        await call.answer(_("plan_not_found"), show_alert=True)
        return
    node_id = getattr(call.bot, "node_id", 0)
    await adjust_plan_for_reseller(plan, call.from_user.id, node_id)
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
    node_id = getattr(call.bot, "node_id", 0)
    await adjust_plan_for_reseller(plan, call.from_user.id, node_id)
        
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


async def _show_bulk_checkout_methods(call: CallbackQuery, plan: Plan, qty: int, state: FSMContext, _: Callable[[str], str]) -> None:
    methods = available_methods(plan)
    node_id = getattr(call.bot, "node_id", 0)
    balance = await repo.get_balance(call.from_user.id, node_id=node_id)
    
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
                    else:
                        await state.update_data(applied_promo_code=None)
                else:
                    await state.update_data(applied_promo_code=None)
            else:
                await state.update_data(applied_promo_code=None)
        else:
            await state.update_data(applied_promo_code=None)

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
        
    if promo_code_str:
        caption += _("promo_discount_line", code=promo_code_str, discount=f"{int(discount_amount):,} {s.fiat_currency}")
        
    if can_pay_with_balance:
        caption += f"\n\n💰 Your balance: {int(balance):,} {s.fiat_currency}"
        
    from bot.keyboards.user_kb import bulk_payment_kb
    await call.message.edit_text(
        caption,
        reply_markup=bulk_payment_kb(plan.id, qty, methods, can_pay_with_balance, promo_code_str, _),
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
        
    node_id = getattr(message.bot, "node_id", 0)
    await adjust_plan_for_reseller(plan, message.from_user.id, node_id)
        
    await _show_bulk_checkout_methods_new_message(message, plan, qty, _)


async def _show_bulk_checkout_methods_new_message(message: Message, plan: Plan, qty: int, state: FSMContext, _: Callable[[str], str]) -> None:
    methods = available_methods(plan)
    node_id = getattr(message.bot, "node_id", 0)
    balance = await repo.get_balance(message.from_user.id, node_id=node_id)
    
    state_data = await state.get_data()
    applied_promo = state_data.get("applied_promo_code")
    discount_amount = 0.0
    promo_code_str = None
    
    if applied_promo:
        promo = await repo.get_promo_code_by_code(applied_promo, node_id=node_id)
        if promo and promo.is_active:
            if promo.expiry_time is None or promo.expiry_time > int(time.time() * 1000):
                if promo.max_uses is None or promo.used_count < promo.max_uses:
                    if not await repo.has_user_used_promo(message.from_user.id, promo.code, node_id=node_id):
                        discount_amount = adjust_plan_for_promo(plan, promo) * qty
                        promo_code_str = promo.code
                    else:
                        await state.update_data(applied_promo_code=None)
                else:
                    await state.update_data(applied_promo_code=None)
            else:
                await state.update_data(applied_promo_code=None)
        else:
            await state.update_data(applied_promo_code=None)

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
        
    if promo_code_str:
        caption += _("promo_discount_line", code=promo_code_str, discount=f"{int(discount_amount):,} {s.fiat_currency}")
        
    if can_pay_with_balance:
        caption += f"\n\n💰 Your balance: {int(balance):,} {s.fiat_currency}"
        
    from bot.keyboards.user_kb import bulk_payment_kb
    await message.answer(
        caption,
        reply_markup=bulk_payment_kb(plan.id, qty, methods, can_pay_with_balance, promo_code_str, _),
    )


# --------------------------- Promo Code Applying (FSM) ---------------------------

@router.callback_query(F.data.startswith("plan:promo_apply:"))
async def user_promo_apply_cb(call: CallbackQuery, state: FSMContext, _: Callable[[str], str]) -> None:
    plan_id = int(call.data.split(":")[2])
    await state.set_state(CheckoutStates.awaiting_promo_code)
    await state.update_data(plan_id=plan_id, bulk_qty=None)
    await call.message.answer(_("enter_promo_code"))
    await call.answer()


@router.callback_query(F.data.startswith("plan:promo_clear:"))
async def user_promo_clear_cb(call: CallbackQuery, state: FSMContext, _: Callable[[str], str]) -> None:
    plan_id = int(call.data.split(":")[2])
    await state.update_data(applied_promo_code=None)
    await _show_plan_details(call, plan_id, state, _)


@router.callback_query(F.data.startswith("bulk:promo_apply:"))
async def user_bulk_promo_apply_cb(call: CallbackQuery, state: FSMContext, _: Callable[[str], str]) -> None:
    parts = call.data.split(":")
    plan_id = int(parts[2])
    qty = int(parts[3])
    await state.set_state(CheckoutStates.awaiting_promo_code)
    await state.update_data(plan_id=plan_id, bulk_qty=qty)
    await call.message.answer(_("enter_promo_code"))
    await call.answer()


@router.callback_query(F.data.startswith("bulk:promo_clear:"))
async def user_bulk_promo_clear_cb(call: CallbackQuery, state: FSMContext, _: Callable[[str], str]) -> None:
    parts = call.data.split(":")
    plan_id = int(parts[2])
    qty = int(parts[3])
    await state.update_data(applied_promo_code=None)
    
    plan = await repo.get_plan(plan_id)
    if plan is None or not plan.is_active:
        await call.answer(_("plan_not_found"), show_alert=True)
        return
    node_id = getattr(call.bot, "node_id", 0)
    await adjust_plan_for_reseller(plan, call.from_user.id, node_id)
    await _show_bulk_checkout_methods(call, plan, qty, state, _)


@router.message(CheckoutStates.awaiting_promo_code, F.text)
async def user_promo_code_handler(message: Message, state: FSMContext, _: Callable[[str], str]) -> None:
    code_text = message.text.strip().upper()
    data = await state.get_data()
    plan_id = data.get("plan_id")
    bulk_qty = data.get("bulk_qty")
    
    if code_text == "/CANCEL":
        await state.set_state(None) # clear state but keep plan_id in data
        if bulk_qty:
            plan = await repo.get_plan(plan_id)
            node_id = getattr(message.bot, "node_id", 0)
            await adjust_plan_for_reseller(plan, message.from_user.id, node_id)
            await _show_bulk_checkout_methods_new_message(message, plan, bulk_qty, state, _)
        else:
            await _show_plan_details(message, plan_id, state, _)
        return
        
    node_id = getattr(message.bot, "node_id", 0)
    promo = await repo.get_promo_code_by_code(code_text, node_id=node_id)
    
    if promo is None or not promo.is_active:
        await message.answer(_("promo_not_found"))
        return
        
    if promo.expiry_time and promo.expiry_time < int(time.time() * 1000):
        await message.answer(_("promo_expired"))
        return
        
    if promo.max_uses is not None and promo.used_count >= promo.max_uses:
        await message.answer(_("promo_max_used"))
        return
        
    if await repo.has_user_used_promo(message.from_user.id, promo.code, node_id=node_id):
        await message.answer(_("promo_already_used"))
        return
        
    # Promo is valid! Save in FSM
    await state.update_data(applied_promo_code=promo.code)
    await state.set_state(None)
    
    plan = await repo.get_plan(plan_id)
    await adjust_plan_for_reseller(plan, message.from_user.id, node_id)
    
    discount_amount = adjust_plan_for_promo(plan, promo)
    if bulk_qty:
        discount_amount *= bulk_qty
        
    from bot.config import get_settings
    cur = get_settings().fiat_currency
    
    await message.answer(
        _("promo_applied", code=promo.code, discount=f"{int(discount_amount):,} {cur}")
    )
    
    if bulk_qty:
        await _show_bulk_checkout_methods_new_message(message, plan, bulk_qty, state, _)
    else:
        await _show_plan_details(message, plan_id, state, _)
