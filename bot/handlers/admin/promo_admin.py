from __future__ import annotations

import logging
import time
from typing import Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import repo
from bot.db.models import PromoCode
from bot.filters import IsAdmin
from bot.keyboards.admin_kb import admin_menu_kb, admin_promos_kb, admin_promo_detail_kb
from bot.states.forms import PromoCodeForm, EditPromoCodeForm

logger = logging.getLogger(__name__)
router = Router(name="admin-promo")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _parse_int(text: str, default: int = 0) -> int:
    text = (text or "").strip()
    try:
        return int(float(text))
    except ValueError:
        return default


async def _promo_detail_caption(promo: PromoCode) -> str:
    status_str = "🟢 Active" if promo.is_active else "⚪️ Disabled"
    
    # Expiry string
    if promo.expiry_time:
        import datetime
        expiry_dt = datetime.datetime.fromtimestamp(
            promo.expiry_time / 1000.0, datetime.timezone.utc
        )
        expiry_str = expiry_dt.strftime("%Y-%m-%d %H:%M UTC")
    else:
        expiry_str = "Never"

    # Uses string
    max_uses_str = str(promo.max_uses) if promo.max_uses is not None else "Unlimited"
    uses_str = f"{promo.used_count} / {max_uses_str}"

    # Discount string
    type_str = "%" if promo.discount_type == "percentage" else ""
    discount_str = f"{int(promo.discount_value):,}{type_str}"

    return (
        f"🎟 <b>Promo Code: {promo.code}</b>\n\n"
        f"Status: <b>{status_str}</b>\n"
        f"Discount: <b>{discount_str}</b>\n"
        f"Discount Type: <b>{promo.discount_type.capitalize()}</b>\n"
        f"Uses: <b>{uses_str}</b>\n"
        f"Expires: <code>{expiry_str}</code>\n"
    )


# ----------------------------- Listing & Toggles -----------------------------

@router.callback_query(F.data == "adm:promos")
async def promos_list(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    node_id = getattr(call.bot, "node_id", 0)
    promos = await repo.list_promo_codes(node_id=node_id)
    await call.message.edit_text(
        "🎟 <b>Promo Codes</b>\n\nManage your discounts here:",
        reply_markup=admin_promos_kb(promos)
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm:promo:toggle:"))
async def promo_toggle(call: CallbackQuery) -> None:
    promo_id = int(call.data.rsplit(":", 1)[1])
    promo = await repo.get_promo_code(promo_id)
    if promo is None:
        await call.answer("Promo code not found.", show_alert=True)
        return
    
    # Toggle active status
    promo = await repo.update_promo_code(promo_id, is_active=not promo.is_active)
    await call.message.edit_text(
        await _promo_detail_caption(promo), reply_markup=admin_promo_detail_kb(promo)
    )
    await call.answer("Updated status.")


@router.callback_query(F.data.startswith("adm:promo:type:"))
async def promo_toggle_type(call: CallbackQuery) -> None:
    promo_id = int(call.data.rsplit(":", 1)[1])
    promo = await repo.get_promo_code(promo_id)
    if promo is None:
        await call.answer("Promo code not found.", show_alert=True)
        return
    
    new_type = "fixed" if promo.discount_type == "percentage" else "percentage"
    # Reset/adjust values to valid ranges if toggling to percentage
    new_val = promo.discount_value
    if new_type == "percentage" and new_val > 100:
        new_val = 10.0  # default safe percentage
        
    promo = await repo.update_promo_code(promo_id, discount_type=new_type, discount_value=new_val)
    await call.message.edit_text(
        await _promo_detail_caption(promo), reply_markup=admin_promo_detail_kb(promo)
    )
    await call.answer(f"Changed type to {new_type}.")


@router.callback_query(F.data.startswith("adm:promo:del:"))
async def promo_delete(call: CallbackQuery) -> None:
    promo_id = int(call.data.rsplit(":", 1)[1])
    await repo.delete_promo_code(promo_id)
    node_id = getattr(call.bot, "node_id", 0)
    promos = await repo.list_promo_codes(node_id=node_id)
    
    # Send a confirmation alert
    await call.answer("Promo code deleted successfully.", show_alert=True)
    await call.message.edit_text(
        "🎟 <b>Promo Codes</b>\n\nManage your discounts here:",
        reply_markup=admin_promos_kb(promos)
    )


@router.callback_query(F.data.regexp(r"^adm:promo:\d+$"))
async def promo_detail(call: CallbackQuery) -> None:
    promo_id = int(call.data.rsplit(":", 1)[1])
    promo = await repo.get_promo_code(promo_id)
    if promo is None:
        await call.answer("Promo code not found.", show_alert=True)
        return
    await call.message.edit_text(
        await _promo_detail_caption(promo), reply_markup=admin_promo_detail_kb(promo)
    )
    await call.answer()


# ----------------------------- Creation Flow (FSM) -----------------------------

@router.callback_query(F.data == "adm:promo:new")
async def promo_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PromoCodeForm.code)
    await call.message.answer(
        "🆕 <b>Create Promo Code</b>\n\n"
        "Please enter the promo code string (e.g. <code>WINTER30</code>):\n"
        "Or type /cancel to abort."
    )
    await call.answer()


@router.message(PromoCodeForm.code)
async def process_promo_code_string(message: Message, state: FSMContext) -> None:
    code_text = message.text.strip().upper()
    if code_text == "/CANCEL":
        await state.clear()
        node_id = getattr(message.bot, "node_id", 0)
        await message.answer("❌ Creation cancelled.", reply_markup=admin_menu_kb(node_id))
        return
        
    if not code_text.isalnum():
        await message.answer("⚠️ Promo code must be alphanumeric. Please try again:")
        return

    node_id = getattr(message.bot, "node_id", 0)
    existing = await repo.get_promo_code_by_code(code_text, node_id=node_id)
    if existing:
        await message.answer("⚠️ A promo code with this name already exists. Please choose a different one:")
        return

    await state.update_data(code=code_text)
    await state.set_state(PromoCodeForm.discount_type)
    
    # Offer discount types
    builder = InlineKeyboardBuilder()
    builder.button(text="Percentage (%)", callback_data="prmtype:percentage")
    builder.button(text="Fixed (Fiat amount)", callback_data="prmtype:fixed")
    builder.adjust(2)
    
    await message.answer(
        f"Code: <b>{code_text}</b>\n\n"
        "Choose the discount type:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(PromoCodeForm.discount_type, F.data.startswith("prmtype:"))
async def process_promo_discount_type(call: CallbackQuery, state: FSMContext) -> None:
    dtype = call.data.split(":")[1]
    await state.update_data(discount_type=dtype)
    await state.set_state(PromoCodeForm.discount_value)
    
    unit_str = "percentage (e.g. <code>20</code> for 20% off)" if dtype == "percentage" else "fixed amount (e.g. <code>50000</code>)"
    await call.message.edit_text(
        f"Discount Type: <b>{dtype.capitalize()}</b>\n\n"
        f"Please enter the discount {unit_str}:"
    )
    await call.answer()


@router.message(PromoCodeForm.discount_value)
async def process_promo_discount_value(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    if raw.lower() == "/cancel":
        await state.clear()
        node_id = getattr(message.bot, "node_id", 0)
        await message.answer("❌ Creation cancelled.", reply_markup=admin_menu_kb(node_id))
        return

    try:
        val = float(raw)
        if val <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Please enter a valid positive number:")
        return

    data = await state.get_data()
    dtype = data.get("discount_type")
    if dtype == "percentage" and val > 100:
        await message.answer("⚠️ Percentage discount cannot exceed 100%. Please enter a value between 1 and 100:")
        return

    await state.update_data(discount_value=val)
    await state.set_state(PromoCodeForm.max_uses)

    # Offer inline button for unlimited
    builder = InlineKeyboardBuilder()
    builder.button(text="♾ Unlimited", callback_data="prmuses:unlimited")
    
    await message.answer(
        "Enter the maximum number of uses (total) allowed for this promo code:\n"
        "Or tap the button below for unlimited uses.",
        reply_markup=builder.as_markup()
    )


@router.callback_query(PromoCodeForm.max_uses, F.data == "prmuses:unlimited")
async def process_promo_max_uses_callback(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(max_uses=None)
    await state.set_state(PromoCodeForm.expiry_days)
    
    # Offer inline button for never expires
    builder = InlineKeyboardBuilder()
    builder.button(text="♾ Never Expires", callback_data="prmexpiry:never")
    
    await call.message.edit_text(
        "Max Uses: <b>Unlimited</b>\n\n"
        "Enter the validity period in <b>days</b> from today (e.g. <code>7</code> for one week):\n"
        "Or tap the button below for no expiry date.",
        reply_markup=builder.as_markup()
    )
    await call.answer()


@router.message(PromoCodeForm.max_uses)
async def process_promo_max_uses_message(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    if raw.lower() == "/cancel":
        await state.clear()
        node_id = getattr(message.bot, "node_id", 0)
        await message.answer("❌ Creation cancelled.", reply_markup=admin_menu_kb(node_id))
        return

    val = _parse_int(raw, -1)
    if val <= 0:
        await message.answer("⚠️ Please enter a positive integer for maximum uses:")
        return

    await state.update_data(max_uses=val)
    await state.set_state(PromoCodeForm.expiry_days)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="♾ Never Expires", callback_data="prmexpiry:never")
    
    await message.answer(
        f"Max Uses: <b>{val}</b>\n\n"
        "Enter the validity period in <b>days</b> from today (e.g. <code>7</code> for one week):\n"
        "Or tap the button below for no expiry date.",
        reply_markup=builder.as_markup()
    )


@router.callback_query(PromoCodeForm.expiry_days, F.data == "prmexpiry:never")
async def process_promo_expiry_callback(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    
    node_id = getattr(call.bot, "node_id", 0)
    promo = await repo.create_promo_code(
        node_id=node_id,
        code=data.get("code"),
        discount_type=data.get("discount_type"),
        discount_value=data.get("discount_value"),
        max_uses=data.get("max_uses"),
        expiry_time=None,
        is_active=True
    )
    
    await call.message.edit_text("✅ Promo code created successfully!")
    await call.message.answer(
        await _promo_detail_caption(promo),
        reply_markup=admin_promo_detail_kb(promo)
    )
    await call.answer()


@router.message(PromoCodeForm.expiry_days)
async def process_promo_expiry_message(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    if raw.lower() == "/cancel":
        await state.clear()
        node_id = getattr(message.bot, "node_id", 0)
        await message.answer("❌ Creation cancelled.", reply_markup=admin_menu_kb(node_id))
        return

    days = _parse_int(raw, -1)
    if days <= 0:
        await message.answer("⚠️ Please enter a valid number of days:")
        return

    data = await state.get_data()
    await state.clear()

    # Calculate expiry timestamp in ms
    expiry_time_ms = int((time.time() + (days * 24 * 3600)) * 1000)

    node_id = getattr(message.bot, "node_id", 0)
    promo = await repo.create_promo_code(
        node_id=node_id,
        code=data.get("code"),
        discount_type=data.get("discount_type"),
        discount_value=data.get("discount_value"),
        max_uses=data.get("max_uses"),
        expiry_time=expiry_time_ms,
        is_active=True
    )

    await message.answer("✅ Promo code created successfully!")
    await message.answer(
        await _promo_detail_caption(promo),
        reply_markup=admin_promo_detail_kb(promo)
    )


# ----------------------------- Editing Fields -----------------------------

@router.callback_query(F.data.startswith("adm:prmedit:"))
async def promo_edit_field_start(call: CallbackQuery, state: FSMContext) -> None:
    _, _, promo_id_raw, field = call.data.split(":")
    promo_id = int(promo_id_raw)
    promo = await repo.get_promo_code(promo_id)
    if promo is None:
        await call.answer("Promo code not found.", show_alert=True)
        return

    await state.set_state(EditPromoCodeForm.value)
    await state.update_data(promo_id=promo_id, field=field)

    if field == "code":
        await call.message.answer(
            f"📝 Current code: <b>{promo.code}</b>\n"
            "Send the new promo code string:\n"
            "Or type /cancel to abort."
        )
    elif field == "value":
        unit = "%" if promo.discount_type == "percentage" else ""
        await call.message.answer(
            f"💰 Current discount value: <b>{int(promo.discount_value)}{unit}</b>\n"
            "Send the new discount value:\n"
            "Or type /cancel to abort."
        )
    await call.answer()


@router.message(EditPromoCodeForm.value)
async def process_promo_edit_value(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    data = await state.get_data()
    promo_id = data.get("promo_id")
    field = data.get("field")
    
    if text.lower() == "/cancel" or promo_id is None:
        await state.clear()
        if promo_id:
            promo = await repo.get_promo_code(promo_id)
            await message.answer("❌ Editing cancelled.")
            await message.answer(
                await _promo_detail_caption(promo),
                reply_markup=admin_promo_detail_kb(promo)
            )
        return

    promo = await repo.get_promo_code(promo_id)
    if not promo:
        await state.clear()
        await message.answer("Promo code not found.")
        return

    if field == "code":
        code_text = text.upper()
        if not code_text.isalnum():
            await message.answer("⚠️ Promo code must be alphanumeric. Try again:")
            return
            
        node_id = getattr(message.bot, "node_id", 0)
        existing = await repo.get_promo_code_by_code(code_text, node_id=node_id)
        if existing and existing.id != promo.id:
            await message.answer("⚠️ This promo code already exists. Try again:")
            return
            
        promo = await repo.update_promo_code(promo_id, code=code_text)
    
    elif field == "value":
        try:
            val = float(text)
            if val <= 0:
                raise ValueError
        except ValueError:
            await message.answer("⚠️ Please enter a positive number:")
            return
            
        if promo.discount_type == "percentage" and val > 100:
            await message.answer("⚠️ Percentage discount cannot exceed 100%. Try again:")
            return
            
        promo = await repo.update_promo_code(promo_id, discount_value=val)

    await state.clear()
    await message.answer("✅ Updated successfully!")
    await message.answer(
        await _promo_detail_caption(promo),
        reply_markup=admin_promo_detail_kb(promo)
    )
