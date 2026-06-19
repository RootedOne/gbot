from __future__ import annotations

import logging
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import repo
from bot.filters import IsAdmin
from bot.keyboards.admin_kb import admin_menu_kb, confirm_kb
from bot.states.forms import ResellerInboundForm

logger = logging.getLogger(__name__)
router = Router(name="main-resellers-admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class ResellerAdminForm(StatesGroup):
    promo_tg_id = State()
    promo_gb_price = State()
    promo_day_price = State()
    promo_unlimited_price = State()
    topup_amount = State()
    edit_gb_price = State()
    edit_day_price = State()
    edit_unlimited_price = State()
    edit_panel_gb_price = State()
    edit_panel_unlimited_price = State()


# ----------------------------- listing -----------------------------

@router.callback_query(F.data == "adm:resellers")
async def resellers_list(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    bot = call.bot
    # Guard to prevent this running on reseller nodes
    if getattr(bot, "node_id", 0) > 0:
        await call.answer("Access Denied.", show_alert=True)
        return

    resellers = await repo.list_resellers()
    text = "👥 <b>Resellers Management</b>\n\n"
    if not resellers:
        text += "No active resellers currently."
    else:
        text += f"Active resellers ({len(resellers)}):"

    kb = InlineKeyboardBuilder()
    for r in resellers:
        name = r.full_name or r.username or f"ID: {r.tg_id}"
        kb.button(text=f"👤 {name} ({int(r.balance):,} tomans)", callback_data=f"resadm:view:{r.tg_id}")
    
    kb.button(text="➕ Promote Reseller", callback_data="resadm:promote")
    kb.button(text="⬅️ Back", callback_data="adm:menu")
    kb.adjust(1)

    await call.message.edit_text(text, reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("resadm:view:"))
async def reseller_view(call: CallbackQuery) -> None:
    tg_id = int(call.data.split(":")[2])
    reseller = await repo.get_user(tg_id, 0)
    if not reseller or not reseller.is_reseller:
        await call.answer("Reseller not found.", show_alert=True)
        return

    nodes = await repo.list_nodes_for_owner(tg_id)
    node_lines = []
    for n in nodes:
        active_str = "🟢 Active" if n.is_active else "🔴 Inactive"
        node_lines.append(f"• {n.brand_name} ({n.bot_username or 'no username'}): {active_str}")
    nodes_text = "\n".join(node_lines) if node_lines else "No connected bots."

    text = (
        f"👤 <b>Reseller Details</b>\n\n"
        f"User ID: <code>{reseller.tg_id}</code>\n"
        f"Name: <b>{reseller.full_name or '—'}</b>\n"
        f"Username: @{reseller.username or '—'}\n\n"
        f"💰 Wallet Balance: <b>{int(reseller.balance):,} tomans</b>\n"
        f"🏷 Custom GB Price: <b>{int(reseller.reseller_gb_price):,} tomans/GB</b>\n"
        f"🏷 Custom Day Price: <b>{int(getattr(reseller, 'reseller_day_price', 0.0)):,} tomans/Day</b>\n"
        f"♾ Custom Unlimited Price: <b>{int(getattr(reseller, 'reseller_unlimited_price', 0.0)):,} tomans</b>\n\n"
        f"🤖 Connected Bots:\n{nodes_text}"
    )

    kb = InlineKeyboardBuilder()
    kb.button(text="💰 Adjust Balance", callback_data=f"resadm:topup:{tg_id}")
    kb.button(text="✏️ Edit GB Price", callback_data=f"resadm:setprice:{tg_id}")
    kb.button(text="✏️ Edit Day Price", callback_data=f"resadm:setdayprice:{tg_id}")
    kb.button(text="✏️ Edit Unlimited Price", callback_data=f"resadm:setunlprice:{tg_id}")
    kb.button(text="🔌 Custom Inbounds", callback_data=f"resadm:inbounds:{tg_id}")
    kb.button(text="❌ Demote Reseller", callback_data=f"resadm:demote:{tg_id}")
    kb.button(text="⬅️ Back", callback_data="adm:resellers")
    kb.adjust(2)

    await call.message.edit_text(text, reply_markup=kb.as_markup())
    await call.answer()


# ----------------------------- promotion -----------------------------

@router.callback_query(F.data == "resadm:promote")
async def reseller_promote_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ResellerAdminForm.promo_tg_id)
    await call.message.answer(
        "📝 Send the <b>Telegram User ID</b> of the user you want to promote to reseller:"
    )
    await call.answer()


@router.message(ResellerAdminForm.promo_tg_id)
async def reseller_promote_tg_id(message: Message, state: FSMContext) -> None:
    val = message.text.strip()
    if not val.isdigit():
        await message.answer("❌ Please enter a numeric Telegram User ID:")
        return
    await state.update_data(tg_id=int(val))
    await state.set_state(ResellerAdminForm.promo_gb_price)
    await message.answer("📝 Send the <b>GB price</b> for this reseller (in tomans):")


@router.message(ResellerAdminForm.promo_gb_price)
async def reseller_promote_gb_price(message: Message, state: FSMContext) -> None:
    val = message.text.strip()
    try:
        price = float(val)
    except ValueError:
        await message.answer("❌ Please enter a numeric price:")
        return

    await state.update_data(gb_price=price)
    await state.set_state(ResellerAdminForm.promo_day_price)
    await message.answer("📝 Send the <b>Extra Day price</b> for this reseller (in tomans):")


@router.message(ResellerAdminForm.promo_day_price)
async def reseller_promote_day_price(message: Message, state: FSMContext) -> None:
    val = message.text.strip()
    try:
        day_price = float(val)
    except ValueError:
        await message.answer("❌ Please enter a numeric price:")
        return

    await state.update_data(day_price=day_price)
    await state.set_state(ResellerAdminForm.promo_unlimited_price)
    await message.answer("📝 Send the <b>Unlimited Plan flat price</b> for this reseller (in tomans):")


@router.message(ResellerAdminForm.promo_unlimited_price)
async def reseller_promote_unlimited_price(message: Message, state: FSMContext) -> None:
    val = message.text.strip()
    try:
        unlimited_price = float(val)
    except ValueError:
        await message.answer("❌ Please enter a numeric price:")
        return

    data = await state.get_data()
    tg_id = data.get("tg_id")
    gb_price = data.get("gb_price")
    day_price = data.get("day_price")
    await state.clear()

    user = await repo.promote_to_reseller(tg_id, gb_price=gb_price, day_price=day_price, unlimited_price=unlimited_price)
    await message.answer(
        f"✅ Promoted user <code>{tg_id}</code> to Reseller.\n"
        f"GB price set to {int(gb_price):,} tomans.\n"
        f"Extra Day price set to {int(day_price):,} tomans.\n"
        f"Unlimited Plan price set to {int(unlimited_price):,} tomans.",
        reply_markup=admin_menu_kb(0)
    )


# ----------------------------- balance adjustment -----------------------------

@router.callback_query(F.data.startswith("resadm:topup:"))
async def reseller_topup_start(call: CallbackQuery, state: FSMContext) -> None:
    tg_id = int(call.data.split(":")[2])
    await state.update_data(target_tg_id=tg_id)
    await state.set_state(ResellerAdminForm.topup_amount)
    await call.message.answer(
        "📝 Enter the amount to adjust (in tomans).\n"
        "Use positive values to add (e.g. <code>50000</code>) and negative to subtract (e.g. <code>-20000</code>):"
    )
    await call.answer()


@router.message(ResellerAdminForm.topup_amount)
async def reseller_topup_process(message: Message, state: FSMContext) -> None:
    val = message.text.strip()
    try:
        amount = float(val)
    except ValueError:
        await message.answer("❌ Please enter a valid number (e.g. 10000 or -5000):")
        return

    data = await state.get_data()
    tg_id = data.get("target_tg_id")
    await state.clear()

    try:
        new_balance = await repo.adjust_balance(
            tg_id=tg_id,
            amount=amount,
            reason="Admin manual balance adjustment",
            admin_id=message.from_user.id,
            allow_negative=True,
            node_id=0,
        )
        await message.answer(
            f"✅ Balance adjusted successfully.\n"
            f"New Balance: <b>{int(new_balance):,} tomans</b>",
            reply_markup=admin_menu_kb(0)
        )
    except Exception as exc:
        await message.answer(f"❌ Failed to adjust balance: {exc}")


# ----------------------------- price editing -----------------------------

@router.callback_query(F.data.startswith("resadm:setprice:"))
async def reseller_edit_price_start(call: CallbackQuery, state: FSMContext) -> None:
    tg_id = int(call.data.split(":")[2])
    await state.update_data(target_tg_id=tg_id)
    await state.set_state(ResellerAdminForm.edit_gb_price)
    await call.message.answer("📝 Send the new <b>GB price</b> for this reseller (in tomans):")
    await call.answer()


@router.message(ResellerAdminForm.edit_gb_price)
async def reseller_edit_price_process(message: Message, state: FSMContext) -> None:
    val = message.text.strip()
    try:
        price = float(val)
    except ValueError:
        await message.answer("❌ Please enter a numeric price:")
        return

    data = await state.get_data()
    tg_id = data.get("target_tg_id")
    await state.clear()

    await repo.promote_to_reseller(tg_id, gb_price=price)
    await message.answer(
        f"✅ GB price updated successfully to {int(price):,} tomans.",
        reply_markup=admin_menu_kb(0)
    )


@router.callback_query(F.data.startswith("resadm:setdayprice:"))
async def reseller_edit_day_price_start(call: CallbackQuery, state: FSMContext) -> None:
    tg_id = int(call.data.split(":")[2])
    await state.update_data(target_tg_id=tg_id)
    await state.set_state(ResellerAdminForm.edit_day_price)
    await call.message.answer("📝 Send the new <b>Extra Day price</b> for this reseller (in tomans):")
    await call.answer()


@router.message(ResellerAdminForm.edit_day_price)
async def reseller_edit_day_price_process(message: Message, state: FSMContext) -> None:
    val = message.text.strip()
    try:
        price = float(val)
    except ValueError:
        await message.answer("❌ Please enter a numeric price:")
        return

    data = await state.get_data()
    tg_id = data.get("target_tg_id")
    await state.clear()

    await repo.promote_to_reseller(tg_id, day_price=price)
    await message.answer(
        f"✅ Extra Day price updated successfully to {int(price):,} tomans.",
        reply_markup=admin_menu_kb(0)
    )


# ----------------------------- demotion -----------------------------

@router.callback_query(F.data.startswith("resadm:demote:"))
async def reseller_demote_confirm(call: CallbackQuery) -> None:
    tg_id = int(call.data.split(":")[2])
    await call.message.edit_text(
        f"Confirm demoting reseller <code>{tg_id}</code>? Their bots and custom plans will remain in the database but won't operate.",
        reply_markup=confirm_kb(
            yes_cb=f"resadm:demotedone:{tg_id}",
            no_cb=f"resadm:view:{tg_id}",
        ),
    )
    await call.answer()


@router.callback_query(F.data.startswith("resadm:demotedone:"))
async def reseller_demote_done(call: CallbackQuery) -> None:
    tg_id = int(call.data.split(":")[2])
    await repo.demote_from_reseller(tg_id)
    await call.message.edit_text("✅ User demoted. They are no longer a reseller.")
    await call.answer()


@router.callback_query(F.data.startswith("resadm:inbounds:"))
async def reseller_inbounds_panels(call: CallbackQuery) -> None:
    reseller_tg_id = int(call.data.split(":")[2])
    panels = await repo.list_panels()
    text = (
        "🔌 <b>Custom Reseller Inbounds & Pricing</b>\n\n"
        "Select the panel you want to configure settings/pricing for this reseller:"
    )
    kb = InlineKeyboardBuilder()
    for p in panels:
        kb.button(text=f"🖥 {p.name}", callback_data=f"resadm:panelmenu:{reseller_tg_id}:{p.id}")
    kb.button(text="⬅️ Back", callback_data=f"resadm:view:{reseller_tg_id}")
    kb.adjust(1)
    await call.message.edit_text(text, reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("resadm:panelmenu:"))
async def reseller_panel_menu(call: CallbackQuery) -> None:
    parts = call.data.split(":")
    reseller_tg_id = int(parts[2])
    panel_id = int(parts[3])
    
    panel = await repo.get_panel(panel_id)
    if not panel:
        await call.answer("Panel not found.", show_alert=True)
        return
        
    custom = await repo.get_reseller_panel_inbounds(reseller_tg_id, panel_id)
    inbounds_str = f"{len(custom.inbound_ids)} allowed" if custom and custom.inbound_ids else "Default (All reseller inbounds)"
    price_str = f"{int(custom.reseller_gb_price):,} tomans/GB" if custom and custom.reseller_gb_price is not None else "Default (Global price)"
    unl_price_str = f"{int(custom.reseller_unlimited_price):,} tomans" if custom and custom.reseller_unlimited_price is not None else "Default (Global price)"
    
    text = (
        f"🖥 <b>Server Settings Override</b>\n\n"
        f"Server: <b>{panel.name}</b>\n"
        f"Reseller ID: <code>{reseller_tg_id}</code>\n\n"
        f"🔌 Inbounds: <b>{inbounds_str}</b>\n"
        f"🏷 GB Price: <b>{price_str}</b>\n"
        f"♾ Unlimited Price: <b>{unl_price_str}</b>\n\n"
        f"Choose an option to modify for this server:"
    )
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🔌 Custom Inbounds", callback_data=f"resadm:inboundspnl:{reseller_tg_id}:{panel_id}")
    kb.button(text="🏷 Set GB Price", callback_data=f"resadm:panelgbprice:{reseller_tg_id}:{panel_id}")
    kb.button(text="♾ Set Unlimited Price", callback_data=f"resadm:panelunlprice:{reseller_tg_id}:{panel_id}")
    kb.button(text="⬅️ Back", callback_data=f"resadm:inbounds:{reseller_tg_id}")
    kb.adjust(1)
    
    await call.message.edit_text(text, reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("resadm:panelgbprice:"))
async def reseller_edit_panel_price_start(call: CallbackQuery, state: FSMContext) -> None:
    parts = call.data.split(":")
    reseller_tg_id = int(parts[2])
    panel_id = int(parts[3])
    
    panel = await repo.get_panel(panel_id)
    if not panel:
        await call.answer("Panel not found.", show_alert=True)
        return
        
    await state.update_data(target_tg_id=reseller_tg_id, panel_id=panel_id)
    await state.set_state(ResellerAdminForm.edit_panel_gb_price)
    
    await call.message.answer(
        f"📝 Send the new <b>GB price</b> (in tomans) for this reseller on panel <b>{panel.name}</b>:\n\n"
        f"To clear the custom price and use the default global price, send <code>default</code> or <code>0</code>."
    )
    await call.answer()


@router.message(ResellerAdminForm.edit_panel_gb_price)
async def reseller_edit_panel_price_process(message: Message, state: FSMContext) -> None:
    val = message.text.strip().lower()
    data = await state.get_data()
    tg_id = data.get("target_tg_id")
    panel_id = data.get("panel_id")
    await state.clear()
    
    if val in ("default", "0"):
        price = None
        price_desc = "cleared (default global price will be used)"
    else:
        try:
            price = float(val)
            price_desc = f"set to {int(price):,} tomans/GB"
        except ValueError:
            await message.answer("❌ Invalid input. Please enter a number or 'default':")
            return
            
    await repo.set_reseller_panel_price(tg_id, panel_id, price)
    
    panel = await repo.get_panel(panel_id)
    panel_name = panel.name if panel else f"Panel #{panel_id}"
    
    await message.answer(
        f"✅ GB price for reseller on panel <b>{panel_name}</b> has been {price_desc}.",
        reply_markup=admin_menu_kb(0)
    )


@router.callback_query(F.data.startswith("resadm:inboundspnl:"))
async def reseller_inbounds_picker_start(call: CallbackQuery, state: FSMContext) -> None:
    from bot.panel.client import get_panel_client
    from bot.keyboards.admin_kb import inbound_picker_kb
    from bot.states.forms import ResellerInboundForm

    parts = call.data.split(":")
    reseller_tg_id = int(parts[2])
    panel_id = int(parts[3])
    panel = await repo.get_panel(panel_id)
    if not panel:
        await call.answer("Panel not found.", show_alert=True)
        return

    try:
        client = await get_panel_client(panel_id)
        options = await client.inbound_options()
    except Exception as exc:
        await call.message.answer(
            f"⚠️ Could not load inbounds: {exc}\n"
            "Send inbound IDs manually as comma-separated numbers (e.g. 3,5):"
        )
        await state.set_state(ResellerInboundForm.inbound_ids)
        await state.update_data(reseller_tg_id=reseller_tg_id, panel_id=panel_id)
        await call.answer()
        return

    # Fetch current whitelist
    custom = await repo.get_reseller_panel_inbounds(reseller_tg_id, panel_id)
    selected = list(custom.inbound_ids or []) if custom else []

    await state.set_state(ResellerInboundForm.inbound_ids)
    await state.update_data(
        reseller_tg_id=reseller_tg_id,
        panel_id=panel_id,
        inbound_ids=selected,
        inbound_options=[
            {"id": o.id, "remark": o.remark, "protocol": o.protocol, "port": o.port}
            for o in options
        ],
    )

    await call.message.answer(
        f"🔌 Select whitelisted inbounds for user <code>{reseller_tg_id}</code> on panel <b>{panel.name}</b>:",
        reply_markup=inbound_picker_kb(options, selected, prefix="resadm:inbset"),
    )
    await call.answer()


def _reseller_inbound_options_from_state(data: dict):
    from bot.panel.schemas import InboundOption
    return [InboundOption.from_api(o) for o in data.get("inbound_options", [])]


@router.callback_query(ResellerInboundForm.inbound_ids, F.data.startswith("resadm:inbset:"))
async def reseller_inbounds_picker_toggle(call: CallbackQuery, state: FSMContext) -> None:
    from bot.keyboards.admin_kb import inbound_picker_kb

    action = call.data.rsplit(":", 1)[1]
    data = await state.get_data()
    selected = list(data.get("inbound_ids", []))
    options = _reseller_inbound_options_from_state(data)
    reseller_tg_id = data.get("reseller_tg_id")
    panel_id = data.get("panel_id")

    if action == "done":
        await repo.set_reseller_panel_inbounds(reseller_tg_id, panel_id, selected)
        await state.clear()
        await call.message.edit_text(f"✅ Allowed inbounds updated: {selected}")
        await call.answer("Updated.")
        
        # Go back to panel choice
        call.data = f"resadm:panelmenu:{reseller_tg_id}:{panel_id}"
        await reseller_panel_menu(call)
        return

    inbound_id = int(action)
    if inbound_id in selected:
        selected.remove(inbound_id)
    else:
        selected.append(inbound_id)
    await state.update_data(inbound_ids=selected)
    await call.message.edit_reply_markup(
        reply_markup=inbound_picker_kb(options, selected, prefix="resadm:inbset")
    )
    await call.answer()


@router.message(ResellerInboundForm.inbound_ids)
async def reseller_inbounds_picker_manual(message: Message, state: FSMContext) -> None:
    ids = [int(p.strip()) for p in (message.text or "").split(",") if p.strip().isdigit()]
    if not ids:
        await message.answer("Please send inbound IDs like: 3,5")
        return
    data = await state.get_data()
    reseller_tg_id = data.get("reseller_tg_id")
    panel_id = data.get("panel_id")
    await state.clear()
    await repo.set_reseller_panel_inbounds(reseller_tg_id, panel_id, ids)
    await message.answer(f"✅ Allowed inbounds updated: {ids}")
    
    # Send quick confirmation and show admin menu
    await message.answer("Reseller settings saved.", reply_markup=admin_menu_kb(0))


# ----------------------------- unlimited price editing -----------------------------

@router.callback_query(F.data.startswith("resadm:setunlprice:"))
async def reseller_edit_unlimited_price_start(call: CallbackQuery, state: FSMContext) -> None:
    tg_id = int(call.data.split(":")[2])
    await state.update_data(target_tg_id=tg_id)
    await state.set_state(ResellerAdminForm.edit_unlimited_price)
    await call.message.answer("📝 Send the new <b>Unlimited Plan price</b> for this reseller (in tomans):")
    await call.answer()


@router.message(ResellerAdminForm.edit_unlimited_price)
async def reseller_edit_unlimited_price_process(message: Message, state: FSMContext) -> None:
    val = message.text.strip()
    try:
        price = float(val)
    except ValueError:
        await message.answer("❌ Please enter a numeric price:")
        return

    data = await state.get_data()
    tg_id = data.get("target_tg_id")
    await state.clear()

    await repo.promote_to_reseller(tg_id, unlimited_price=price)
    await message.answer(
        f"✅ Unlimited Plan price updated successfully to {int(price):,} tomans.",
        reply_markup=admin_menu_kb(0)
    )


@router.callback_query(F.data.startswith("resadm:panelunlprice:"))
async def reseller_edit_panel_unlimited_price_start(call: CallbackQuery, state: FSMContext) -> None:
    parts = call.data.split(":")
    reseller_tg_id = int(parts[2])
    panel_id = int(parts[3])
    
    panel = await repo.get_panel(panel_id)
    if not panel:
        await call.answer("Panel not found.", show_alert=True)
        return
        
    await state.update_data(target_tg_id=reseller_tg_id, panel_id=panel_id)
    await state.set_state(ResellerAdminForm.edit_panel_unlimited_price)
    
    await call.message.answer(
        f"📝 Send the new <b>Unlimited Plan price</b> (in tomans) for this reseller on panel <b>{panel.name}</b>:\n\n"
        f"To clear the custom price and use the default global price, send <code>default</code> or <code>0</code>."
    )
    await call.answer()


@router.message(ResellerAdminForm.edit_panel_unlimited_price)
async def reseller_edit_panel_unlimited_price_process(message: Message, state: FSMContext) -> None:
    val = message.text.strip().lower()
    data = await state.get_data()
    tg_id = data.get("target_tg_id")
    panel_id = data.get("panel_id")
    await state.clear()
    
    if val in ("default", "0"):
        price = None
        price_desc = "cleared (default global price will be used)"
    else:
        try:
            price = float(val)
            price_desc = f"set to {int(price):,} tomans"
        except ValueError:
            await message.answer("❌ Invalid input. Please enter a number or 'default':")
            return
            
    await repo.set_reseller_panel_price(tg_id, panel_id, unlimited_price=price)
    
    panel = await repo.get_panel(panel_id)
    panel_name = panel.name if panel else f"Panel #{panel_id}"
    
    await message.answer(
        f"✅ Unlimited Plan price for reseller on panel <b>{panel_name}</b> has been {price_desc}.",
        reply_markup=admin_menu_kb(0)
    )
