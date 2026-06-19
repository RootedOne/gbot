from __future__ import annotations

import logging
from aiogram import F, Router, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import repo
from bot.keyboards.admin_kb import confirm_kb
from bot.services.nodes.manager import start_node_bot, stop_node_bot

logger = logging.getLogger(__name__)
router = Router(name="user-reseller-panel")


class ResellerNodeForm(StatesGroup):
    bot_token = State()
    brand_name = State()
    support_contact = State()
    card_number = State()
    card_holder = State()


# Helper to check reseller status on Main Bot
async def _is_reseller_check(user_id: int, bot) -> bool:
    if getattr(bot, "node_id", 0) > 0:
        return False
    user = await repo.get_user(user_id, 0)
    return bool(user and user.is_reseller)


@router.message(F.text.in_(["💼 Reseller Panel", "💼 پنل نماینده"]))
async def reseller_panel_menu_msg(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not await _is_reseller_check(message.from_user.id, message.bot):
        return

    await _show_reseller_panel(message, message.from_user.id)


@router.callback_query(F.data == "reseller:menu")
async def reseller_panel_menu_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not await _is_reseller_check(call.from_user.id, call.bot):
        await call.answer("Access Denied.", show_alert=True)
        return

    await _show_reseller_panel(call.message, call.from_user.id, edit=True)
    await call.answer()


async def _show_reseller_panel(target: Message, user_id: int, edit: bool = False) -> None:
    reseller = await repo.get_user(user_id, 0)
    nodes = await repo.list_nodes_for_owner(user_id)

    panels = await repo.list_reseller_panels(only_active=True)
    custom_prices = []
    for p in panels:
        custom = await repo.get_reseller_panel_inbounds(user_id, p.id)
        if custom:
            parts = []
            if custom.reseller_gb_price is not None:
                parts.append(f"{int(custom.reseller_gb_price):,} tomans/GB")
            if custom.reseller_unlimited_price is not None:
                parts.append(f"♾ {int(custom.reseller_unlimited_price):,} tomans")
            if parts:
                custom_prices.append(f"• {p.name}: <b>{' / '.join(parts)}</b>")

    custom_prices_str = ""
    if custom_prices:
        custom_prices_str = "\n🖥 <b>Custom Server Prices:</b>\n" + "\n".join(custom_prices) + "\n"

    reseller_unl_price = getattr(reseller, "reseller_unlimited_price", 0.0)
    text = (
        "💼 <b>Reseller Panel</b>\n\n"
        f"💰 Your Balance: <b>{int(reseller.balance):,} tomans</b>\n"
        f"🏷 Default GB Cost: <b>{int(reseller.reseller_gb_price):,} tomans/GB</b>\n"
        f"♾ Default Unlimited Cost: <b>{int(reseller_unl_price):,} tomans</b>\n"
        f"{custom_prices_str}"
        f"🏷 Your Day Cost: <b>{int(getattr(reseller, 'reseller_day_price', 0.0)):,} tomans/Day</b>\n\n"
        "Here you can connect new Telegram bots as nodes, toggle them on/off, "
        "and configure brand and billing details for your customers."
    )

    kb = InlineKeyboardBuilder()
    if nodes:
        for node in nodes:
            status = "🟢" if node.is_active else "🔴"
            kb.button(
                text=f"{status} {node.brand_name} ({node.bot_username or 'bot'})",
                callback_data=f"resnode:view:{node.id}"
            )
    kb.button(text="➕ Connect New Bot", callback_data="resnode:connect")
    kb.adjust(1)

    if edit:
        await target.edit_text(text, reply_markup=kb.as_markup())
    else:
        await target.answer(text, reply_markup=kb.as_markup())


# ----------------------------- connect bot -----------------------------

@router.callback_query(F.data == "resnode:connect")
async def connect_bot_start(call: CallbackQuery, state: FSMContext) -> None:
    if not await _is_reseller_check(call.from_user.id, call.bot):
        await call.answer("Access Denied.", show_alert=True)
        return

    await state.set_state(ResellerNodeForm.bot_token)
    await call.message.answer(
        "🤖 Send your Telegram **Bot Token** (obtained from @BotFather):\n"
        "Ensure the token is correct. The bot will immediately connect."
    )
    await call.answer()


@router.message(ResellerNodeForm.bot_token)
async def connect_bot_token(message: Message, state: FSMContext, dispatcher: Dispatcher) -> None:
    if not await _is_reseller_check(message.from_user.id, message.bot):
        return

    token = message.text.strip()
    if ":" not in token:
        await message.answer("❌ Invalid Bot Token format. Please send a valid token:")
        return

    # Check if this token is already used
    existing = await repo.get_node_by_token(token)
    if existing:
        await message.answer("❌ This bot token is already registered by another user.")
        return

    await message.answer("⏳ Validating token and starting bot...")
    
    # Pre-create the node in inactive state
    try:
        node = await repo.create_node(
            owner_tg_id=message.from_user.id,
            bot_token=token,
            is_active=False
        )
    except Exception as exc:
        await message.answer(f"❌ Failed to save bot node: {exc}")
        await state.clear()
        return

    # Try starting it
    success = await start_node_bot(token, node.id, dispatcher)
    if success:
        await repo.update_node(node.id, is_active=True)
        await message.answer(
            "✅ <b>Bot connected successfully!</b>\n"
            "Your node bot is now active and running. Go to the Reseller Panel to configure its card details and plans.",
            reply_markup=InlineKeyboardBuilder().button(text="💼 Reseller Panel", callback_data="reseller:menu").as_markup()
        )
    else:
        # Delete or keep inactive
        await repo.update_node(node.id, is_active=False)
        await message.answer(
            "❌ Failed to initialize bot. Please make sure the token is valid and not blocked, then try again."
        )

    await state.clear()


# ----------------------------- view node -----------------------------

async def _show_node_details(call: CallbackQuery, node_id: int) -> None:
    node = await repo.get_node(node_id)
    if not node or node.owner_tg_id != call.from_user.id:
        await call.answer("Node bot not found.", show_alert=True)
        return

    users_count = await repo.count_users(node.id)
    active_services = await repo.count_active_services(node.id)

    status_str = "🟢 Active (Polling)" if node.is_active else "🔴 Inactive (Stopped)"
    text = (
        f"🤖 <b>Bot Node Details</b>\n\n"
        f"Username: <b>{node.bot_username or '—'}</b>\n"
        f"Status: {status_str}\n\n"
        f"⚙️ <b>Settings:</b>\n"
        f"Brand Name: <b>{node.brand_name}</b>\n"
        f"Support Contact: <b>{node.support_contact}</b>\n"
        f"Card Number: <code>{node.card_number or '—'}</code>\n"
        f"Card Holder: <b>{node.card_holder or '—'}</b>\n\n"
        f"📊 <b>Statistics:</b>\n"
        f"Users: <b>{users_count}</b>\n"
        f"Active Services: <b>{active_services}</b>"
    )

    kb = InlineKeyboardBuilder()
    toggle_text = "🔴 Stop Bot" if node.is_active else "🟢 Start Bot"
    kb.button(text=toggle_text, callback_data=f"resnode:toggle:{node.id}")
    kb.button(text="✏️ Edit Settings", callback_data=f"resnode:edit:{node.id}")
    kb.button(text="⬅️ Back", callback_data="reseller:menu")
    kb.adjust(2)

    await call.message.edit_text(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("resnode:view:"))
async def view_node_details(call: CallbackQuery) -> None:
    if not await _is_reseller_check(call.from_user.id, call.bot):
        await call.answer("Access Denied.", show_alert=True)
        return

    node_id = int(call.data.split(":")[2])
    await _show_node_details(call, node_id)
    await call.answer()


# ----------------------------- start/stop bot -----------------------------

@router.callback_query(F.data.startswith("resnode:toggle:"))
async def toggle_node_active(call: CallbackQuery, dispatcher: Dispatcher) -> None:
    if not await _is_reseller_check(call.from_user.id, call.bot):
        await call.answer("Access Denied.", show_alert=True)
        return

    node_id = int(call.data.split(":")[2])
    node = await repo.get_node(node_id)
    if not node or node.owner_tg_id != call.from_user.id:
        await call.answer("Node bot not found.", show_alert=True)
        return

    if node.is_active:
        await stop_node_bot(node.id)
        await repo.update_node(node.id, is_active=False)
        await call.answer("Bot node stopped successfully.")
    else:
        success = await start_node_bot(node.bot_token, node.id, dispatcher)
        if success:
            await repo.update_node(node.id, is_active=True)
            await call.answer("Bot node started successfully.")
        else:
            await call.answer("Failed to start bot. Check token.", show_alert=True)

    # Refresh view
    await _show_node_details(call, node.id)


# ----------------------------- edit node settings -----------------------------

@router.callback_query(F.data.startswith("resnode:edit:"))
async def edit_node_settings(call: CallbackQuery) -> None:
    node_id = int(call.data.split(":")[2])
    
    text = "📝 Select a setting field you want to modify for your Node Bot:"
    kb = InlineKeyboardBuilder()
    kb.button(text="Brand Name", callback_data=f"resnode:setfield:{node_id}:brand_name")
    kb.button(text="Support Contact", callback_data=f"resnode:setfield:{node_id}:support_contact")
    kb.button(text="Card Number", callback_data=f"resnode:setfield:{node_id}:card_number")
    kb.button(text="Card Holder", callback_data=f"resnode:setfield:{node_id}:card_holder")
    kb.button(text="⬅️ Back", callback_data=f"resnode:view:{node_id}")
    kb.adjust(2)

    await call.message.edit_text(text, reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("resnode:setfield:"))
async def edit_node_field_start(call: CallbackQuery, state: FSMContext) -> None:
    parts = call.data.split(":")
    node_id = int(parts[2])
    field = parts[3]

    node = await repo.get_node(node_id)
    if not node or node.owner_tg_id != call.from_user.id:
        await call.answer("Node not found.", show_alert=True)
        return

    await state.update_data(node_id=node_id, field=field)
    
    prompts = {
        "brand_name": "Send the new **Brand Name** for your bot (e.g. Venus VPN):",
        "support_contact": "Send the new **Support Contact** (e.g. @support_user or link):",
        "card_number": "Send the new **Card Number** for customer card-to-card payments:",
        "card_holder": "Send the **Card Holder Name**:"
    }
    
    state_mapping = {
        "brand_name": ResellerNodeForm.brand_name,
        "support_contact": ResellerNodeForm.support_contact,
        "card_number": ResellerNodeForm.card_number,
        "card_holder": ResellerNodeForm.card_holder,
    }

    await state.set_state(state_mapping[field])
    await call.message.answer(prompts[field])
    await call.answer()


@router.message(ResellerNodeForm.brand_name)
@router.message(ResellerNodeForm.support_contact)
@router.message(ResellerNodeForm.card_number)
@router.message(ResellerNodeForm.card_holder)
async def process_edit_field(message: Message, state: FSMContext) -> None:
    if not await _is_reseller_check(message.from_user.id, message.bot):
        return

    val = message.text.strip()
    data = await state.get_data()
    node_id = data.get("node_id")
    field = data.get("field")
    await state.clear()

    node = await repo.get_node(node_id)
    if not node or node.owner_tg_id != message.from_user.id:
        await message.answer("❌ Node not found.")
        return

    await repo.update_node(node_id, **{field: val})

    # Update cache if active
    if node.is_active:
        # Load fresh node
        updated_node = await repo.get_node(node_id)
        from bot.config import update_node_cache
        update_node_cache(
            node_id=node_id,
            owner_tg_id=updated_node.owner_tg_id,
            brand_name=updated_node.brand_name,
            support_contact=updated_node.support_contact,
            card_number=updated_node.card_number,
            card_holder=updated_node.card_holder,
        )

    await message.answer(
        f"✅ Setting `{field}` has been updated to: <b>{val}</b>",
        reply_markup=InlineKeyboardBuilder().button(text="⬅️ Back to Node", callback_data=f"resnode:view:{node_id}").as_markup()
    )
