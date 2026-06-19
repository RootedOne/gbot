from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import repo
from bot.filters import IsAdmin
from typing import Optional
from bot.keyboards.admin_kb import (
    admin_menu_kb,
    admin_plan_detail_kb,
    admin_plans_kb,
    confirm_kb,
    inbound_picker_kb,
    panel_picker_kb,
    plan_edit_menu_kb,
    plan_addon_pricing_kb,
)
from bot.panel.client import PanelError, get_panel_client
from bot.services.pricing import plan_caption
from bot.states.forms import EditPlanForm, PlanForm
from bot.db.models import Plan, PaymentMethod

logger = logging.getLogger(__name__)
router = Router(name="admin-plans")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _parse_int(text: str, default: int = 0) -> int:
    text = (text or "").strip()
    if text in ("", "-", "0"):
        return 0 if text != "" else default
    try:
        return int(float(text))
    except ValueError:
        return default


async def _panel_label(panel_id: int | None) -> str:
    if not panel_id:
        return "—"
    panel = await repo.get_panel(panel_id)
    return panel.name if panel else f"#{panel_id}"


async def _plan_detail_caption(plan) -> str:
    panel_name = await _panel_label(plan.panel_id)
    return (
        plan_caption(plan)
        + f"\n\n🖥 Panel: {panel_name}"
        + f"\n🔌 Inbounds: {plan.inbound_ids or '—'}"
    )


# ----------------------------- listing -----------------------------

@router.callback_query(F.data == "adm:plans")
async def plans_list(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    node_id = getattr(call.bot, "node_id", 0)
    plans = await repo.list_plans(only_active=False, node_id=node_id)
    await call.message.edit_text(
        "🗂 <b>Plans</b>", reply_markup=admin_plans_kb(plans)
    )
    await call.answer()



@router.callback_query(F.data.startswith("adm:plan:toggle:"))
async def plan_toggle(call: CallbackQuery) -> None:
    plan_id = int(call.data.rsplit(":", 1)[1])
    plan = await repo.get_plan(plan_id)
    if plan is None:
        await call.answer("Not found.", show_alert=True)
        return
    plan = await repo.update_plan(plan_id, is_active=not plan.is_active)
    await call.message.edit_text(
        await _plan_detail_caption(plan), reply_markup=admin_plan_detail_kb(plan)
    )
    await call.answer("Updated.")


@router.callback_query(F.data.startswith("adm:plan:del:"))
async def plan_delete(call: CallbackQuery) -> None:
    plan_id = int(call.data.rsplit(":", 1)[1])
    await repo.delete_plan(plan_id)
    node_id = getattr(call.bot, "node_id", 0)
    plans = await repo.list_plans(only_active=False, node_id=node_id)
    await call.message.edit_text(
        "🗑 Plan deleted.", reply_markup=admin_plans_kb(plans)
    )
    await call.answer("Deleted.")



@router.callback_query(F.data.regexp(r"^adm:plan:\d+$"))
async def plan_detail(call: CallbackQuery) -> None:
    plan_id = int(call.data.rsplit(":", 1)[1])
    plan = await repo.get_plan(plan_id)
    if plan is None:
        await call.answer("Not found.", show_alert=True)
        return
    await call.message.edit_text(
        await _plan_detail_caption(plan), reply_markup=admin_plan_detail_kb(plan)
    )
    await call.answer()


# ----------------------------- editing -----------------------------

_EDIT_FIELDS = {
    "title": ("title", "Send the new <b>title</b>:", "text"),
    "desc": ("description", "Send the new <b>description</b> (or '-' to clear):", "opt_text"),
    "traffic": ("traffic_gb", "Send the new <b>traffic in GB</b> (0 = unlimited):", "int"),
    "duration": ("duration_days", "Send the new <b>duration in days</b> (0 = never):", "int"),
    "limitip": ("limit_ip", "Send the new <b>device/IP limit</b> (0 = unlimited):", "int"),
    "price_fiat": ("price_fiat", "Send the new <b>card/fiat price</b> (0 = disable):", "float"),
    "price_stars": ("price_stars", "Send the new <b>Stars price</b> (0 = disable):", "int"),
    "price_usd": ("price_usd", "Send the new <b>USD price</b> (0 = disable):", "float"),
}


async def _show_edit_menu(message: Message, plan) -> None:
    caption = (
        "✏️ <b>Edit plan</b>\n\n"
        + await _plan_detail_caption(plan)
        + "\n\nChoose a field to edit:"
    )
    await message.answer(caption, reply_markup=plan_edit_menu_kb(plan))


@router.callback_query(F.data.startswith("adm:pedit:"))
async def plan_edit_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    plan_id = int(call.data.rsplit(":", 1)[1])
    plan = await repo.get_plan(plan_id)
    if plan is None:
        await call.answer("Not found.", show_alert=True)
        return
    caption = (
        "✏️ <b>Edit plan</b>\n\n"
        + await _plan_detail_caption(plan)
        + "\n\nChoose a field to edit:"
    )
    await call.message.edit_text(caption, reply_markup=plan_edit_menu_kb(plan))
    await call.answer()


@router.callback_query(F.data.startswith("adm:pfield:"))
async def plan_edit_field(call: CallbackQuery, state: FSMContext) -> None:
    _, _, plan_id_raw, field = call.data.split(":")
    plan_id = int(plan_id_raw)
    plan = await repo.get_plan(plan_id)
    if plan is None:
        await call.answer("Not found.", show_alert=True)
        return

    if field == "inbounds":
        if not plan.panel_id:
            await call.answer("Assign a panel first.", show_alert=True)
            return
        await _start_inbound_edit(call, state, plan)
        return

    if field == "panel":
        node_id = getattr(call.bot, "node_id", 0)
        if node_id > 0:
            panels = await repo.list_reseller_panels(only_active=True)
        else:
            panels = await repo.list_panels(only_active=True)
        if not panels:
            await call.answer("No active panels available.", show_alert=True)
            return
        await state.set_state(EditPlanForm.panel_confirm)
        await state.update_data(plan_id=plan_id, new_panel_id=None)
        await call.message.answer(
            "🖥 Choose the panel for this plan.\n"
            "⚠️ Changing panel will clear inbound selections.",
            reply_markup=panel_picker_kb(panels, prefix="adm:planeditpanel"),
        )
        await call.answer()
        return


    meta = _EDIT_FIELDS.get(field)
    if meta is None:
        await call.answer("Unknown field.", show_alert=True)
        return
    await state.set_state(EditPlanForm.value)
    await state.update_data(plan_id=plan_id, field=field)
    await call.message.answer(meta[1])
    await call.answer()


@router.callback_query(EditPlanForm.panel_confirm, F.data.startswith("adm:planeditpanel:"))
async def plan_edit_panel_pick(call: CallbackQuery, state: FSMContext) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    data = await state.get_data()
    plan_id = data.get("plan_id")
    plan = await repo.get_plan(plan_id)
    if plan is None:
        await state.clear()
        await call.answer("Plan not found.", show_alert=True)
        return
    if plan.panel_id == panel_id:
        await state.clear()
        await call.answer("Already on this panel.")
        return
    await state.update_data(new_panel_id=panel_id)
    await call.message.edit_text(
        "Confirm panel change? Inbounds will be cleared.",
        reply_markup=confirm_kb(
            f"adm:planpanelok:{plan_id}:{panel_id}",
            f"adm:pedit:{plan_id}",
        ),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm:planpanelok:"))
async def plan_edit_panel_confirm(call: CallbackQuery, state: FSMContext) -> None:
    _, _, plan_id_raw, panel_id_raw = call.data.split(":")
    plan_id = int(plan_id_raw)
    panel_id = int(panel_id_raw)
    await state.clear()
    plan = await repo.update_plan(plan_id, panel_id=panel_id, inbound_ids=[])
    if plan is None:
        await call.answer("Not found.", show_alert=True)
        return
    await call.message.edit_text(f"✅ Panel updated. Inbounds cleared.")
    await _show_edit_menu(call.message, plan)
    await call.answer()


@router.message(EditPlanForm.value)
async def plan_edit_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data.get("field")
    plan_id = data.get("plan_id")
    meta = _EDIT_FIELDS.get(field)
    if meta is None or plan_id is None:
        await state.clear()
        await message.answer("Edit session expired. Open the plan again.")
        return

    attr, _prompt, kind = meta
    raw = (message.text or "").strip()
    if kind == "text":
        if not raw:
            await message.answer("Please send a non-empty value.")
            return
        value = raw
    elif kind == "opt_text":
        value = "" if raw == "-" else raw
    elif kind == "int":
        value = _parse_int(raw)
    elif kind == "float":
        value = float(_parse_int(raw))
    else:
        value = raw

    await state.clear()
    plan = await repo.update_plan(plan_id, **{attr: value})
    if plan is None:
        await message.answer("Plan not found.")
        return
    await message.answer("✅ Updated.")
    await _show_edit_menu(message, plan)


async def _start_inbound_edit(call: CallbackQuery, state: FSMContext, plan) -> None:
    try:
        client = await get_panel_client(plan.panel_id)
        options = await client.inbound_options()
    except PanelError as exc:
        await state.set_state(EditPlanForm.inbounds)
        await state.update_data(plan_id=plan.id, inbound_ids=list(plan.inbound_ids or []))
        await call.message.answer(
            f"⚠️ Could not load inbounds: {exc}\n"
            "Send inbound IDs manually as comma-separated numbers (e.g. 3,5):"
        )
        await call.answer()
        return

    node_id = getattr(call.bot, "node_id", 0)
    if node_id > 0:
        node = await repo.get_node(node_id)
        if node:
            allowed = await repo.list_allowed_inbounds(node.owner_tg_id, plan.panel_id)
            if allowed is not None:
                options = [o for o in options if o.id in allowed]
                if not options:
                    await call.message.answer(
                        "⚠️ No whitelisted inbounds are available for you on this panel. Please contact the main administrator."
                    )
                    await call.answer()
                    return

    selected = list(plan.inbound_ids or [])
    await state.set_state(EditPlanForm.inbounds)
    await state.update_data(
        plan_id=plan.id,
        inbound_ids=selected,
        inbound_options=[
            {"id": o.id, "remark": o.remark, "protocol": o.protocol, "port": o.port}
            for o in options
        ],
    )
    await call.message.answer(
        "🔌 Select the inbounds for this plan:",
        reply_markup=inbound_picker_kb(options, selected),
    )
    await call.answer()


@router.callback_query(EditPlanForm.inbounds, F.data.startswith("adm:inb:"))
async def plan_edit_inbound_toggle(call: CallbackQuery, state: FSMContext) -> None:
    action = call.data.rsplit(":", 1)[1]
    data = await state.get_data()
    selected = list(data.get("inbound_ids", []))
    options = _options_from_state(data)
    plan_id = data.get("plan_id")

    if action == "done":
        if not selected:
            await call.answer("Select at least one inbound.", show_alert=True)
            return
        await state.clear()
        plan = await repo.update_plan(plan_id, inbound_ids=selected)
        await call.message.edit_text(f"✅ Inbounds updated: {selected}")
        if plan is not None:
            await _show_edit_menu(call.message, plan)
        await call.answer()
        return

    inbound_id = int(action)
    if inbound_id in selected:
        selected.remove(inbound_id)
    else:
        selected.append(inbound_id)
    await state.update_data(inbound_ids=selected)
    await call.message.edit_reply_markup(
        reply_markup=inbound_picker_kb(options, selected)
    )
    await call.answer()


@router.message(EditPlanForm.inbounds)
async def plan_edit_inbounds_manual(message: Message, state: FSMContext) -> None:
    ids = [int(p.strip()) for p in (message.text or "").split(",") if p.strip().isdigit()]
    if not ids:
        await message.answer("Please send inbound IDs like: 3,5")
        return
    data = await state.get_data()
    plan_id = data.get("plan_id")
    await state.clear()
    plan = await repo.update_plan(plan_id, inbound_ids=ids)
    if plan is None:
        await message.answer("Plan not found.")
        return
    await message.answer(f"✅ Inbounds updated: {ids}")
    await _show_edit_menu(message, plan)


# --------------------------- creation FSM ---------------------------

@router.callback_query(F.data == "adm:plan:new")
async def plan_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PlanForm.title)
    await call.message.answer("🆕 <b>New plan</b>\n\nSend the plan <b>title</b>:")
    await call.answer()


@router.message(PlanForm.title)
async def plan_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip())
    await state.set_state(PlanForm.description)
    await message.answer("Send a short <b>description</b> (or '-' to skip):")


@router.message(PlanForm.description)
async def plan_description(message: Message, state: FSMContext) -> None:
    desc = "" if message.text.strip() == "-" else message.text.strip()
    await state.update_data(description=desc)
    await state.set_state(PlanForm.traffic_gb)
    await message.answer("Send <b>traffic in GB</b> (0 = unlimited):")


@router.message(PlanForm.traffic_gb)
async def plan_traffic(message: Message, state: FSMContext) -> None:
    await state.update_data(traffic_gb=_parse_int(message.text))
    await state.set_state(PlanForm.duration_days)
    await message.answer("Send <b>duration in days</b> (0 = never expires):")


@router.message(PlanForm.duration_days)
async def plan_duration(message: Message, state: FSMContext) -> None:
    await state.update_data(duration_days=_parse_int(message.text))
    await state.set_state(PlanForm.limit_ip)
    await message.answer("Send <b>device/IP limit</b> (0 = unlimited):")


@router.message(PlanForm.limit_ip)
async def plan_limit_ip(message: Message, state: FSMContext) -> None:
    await state.update_data(limit_ip=_parse_int(message.text), inbound_ids=[])
    await state.set_state(PlanForm.panel)
    node_id = getattr(message.bot, "node_id", 0)
    if node_id > 0:
        panels = await repo.list_reseller_panels(only_active=True)
    else:
        panels = await repo.list_panels(only_active=True)
    if not panels:
        await state.clear()
        await message.answer(
            "⚠️ No panels available for this bot. Please contact the main administrator.",
            reply_markup=admin_menu_kb(node_id),
        )
        return
    await message.answer(
        "🖥 Choose the <b>panel</b> this plan provisions on:",
        reply_markup=panel_picker_kb(panels),
    )



@router.callback_query(PlanForm.panel, F.data.startswith("adm:planpanel:"))
async def plan_pick_panel(call: CallbackQuery, state: FSMContext) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    await state.update_data(panel_id=panel_id)
    await state.set_state(PlanForm.inbounds)
    try:
        client = await get_panel_client(panel_id)
        options = await client.inbound_options()
    except PanelError as exc:
        await call.message.edit_text(
            f"⚠️ Could not load inbounds from the panel: {exc}\n"
            "Send inbound IDs manually as comma-separated numbers (e.g. 3,5):"
        )
        await call.answer()
        return
    node_id = getattr(call.bot, "node_id", 0)
    if node_id > 0:
        node = await repo.get_node(node_id)
        if node:
            allowed = await repo.list_allowed_inbounds(node.owner_tg_id, panel_id)
            if allowed is not None:
                options = [o for o in options if o.id in allowed]
                if not options:
                    await state.clear()
                    await call.message.edit_text(
                        "⚠️ No whitelisted inbounds are available for you on this panel. Please contact the main administrator."
                    )
                    await call.answer()
                    return

    if not options:
        await call.message.edit_text(
            "No inbounds found on the panel. Send inbound IDs manually "
            "(comma-separated, e.g. 3,5):"
        )
        await call.answer()
        return
    await state.update_data(
        inbound_options=[
            {"id": o.id, "remark": o.remark, "protocol": o.protocol, "port": o.port}
            for o in options
        ]
    )
    await call.message.edit_text("Panel selected.")
    await call.message.answer(
        "🔌 Select the inbounds this plan attaches to:",
        reply_markup=inbound_picker_kb(options, []),
    )
    await call.answer()


def _options_from_state(data: dict):
    from bot.panel.schemas import InboundOption

    return [InboundOption.from_api(o) for o in data.get("inbound_options", [])]


@router.message(PlanForm.inbounds)
async def plan_inbounds_manual(message: Message, state: FSMContext) -> None:
    """Fallback: accept comma-separated inbound IDs typed by the admin."""
    ids = []
    for part in (message.text or "").split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    if not ids:
        await message.answer("Please send inbound IDs like: 3,5")
        return
    await state.update_data(inbound_ids=ids)
    await state.set_state(PlanForm.price_fiat)
    await message.answer(
        f"Selected inbounds: {ids}\n\nSend the <b>card/fiat price</b> (0 = disable):"
    )


@router.callback_query(PlanForm.inbounds, F.data.startswith("adm:inb:"))
async def plan_inbound_toggle(call: CallbackQuery, state: FSMContext) -> None:
    action = call.data.rsplit(":", 1)[1]
    data = await state.get_data()
    selected = list(data.get("inbound_ids", []))
    options = _options_from_state(data)

    if action == "done":
        if not selected:
            await call.answer("Select at least one inbound.", show_alert=True)
            return
        await state.set_state(PlanForm.price_fiat)
        await call.message.edit_text(f"Selected inbounds: {selected}")
        await call.message.answer(
            "Send the <b>card/fiat price</b> (0 = disable this method):"
        )
        await call.answer()
        return

    inbound_id = int(action)
    if inbound_id in selected:
        selected.remove(inbound_id)
    else:
        selected.append(inbound_id)
    await state.update_data(inbound_ids=selected)
    await call.message.edit_reply_markup(
        reply_markup=inbound_picker_kb(options, selected)
    )
    await call.answer()


@router.message(PlanForm.price_fiat)
async def plan_price_fiat(message: Message, state: FSMContext) -> None:
    await state.update_data(price_fiat=float(_parse_int(message.text)))
    await state.set_state(PlanForm.price_stars)
    await message.answer("Send the <b>Telegram Stars</b> price (0 = disable):")


@router.message(PlanForm.price_stars)
async def plan_price_stars(message: Message, state: FSMContext) -> None:
    await state.update_data(price_stars=_parse_int(message.text))
    await state.set_state(PlanForm.price_usd)
    await message.answer("Send the <b>crypto price in USD</b> (0 = disable):")


@router.message(PlanForm.price_usd)
async def plan_price_usd(message: Message, state: FSMContext) -> None:
    try:
        usd = float((message.text or "0").strip())
    except ValueError:
        usd = 0.0
    data = await state.get_data()
    await state.clear()

    node_id = getattr(message.bot, "node_id", 0)
    plan = await repo.create_plan(
        node_id=node_id,
        title=data.get("title", "Plan"),
        description=data.get("description", ""),
        traffic_gb=data.get("traffic_gb", 0),
        duration_days=data.get("duration_days", 30),
        limit_ip=data.get("limit_ip", 0),
        inbound_ids=data.get("inbound_ids", []),
        panel_id=data.get("panel_id"),
        price_fiat=data.get("price_fiat", 0.0),
        price_stars=data.get("price_stars", 0),
        price_usd=usd,
        is_active=True,
    )
    await message.answer(
        "✅ <b>Plan created!</b>\n\n"
        + await _plan_detail_caption(plan),
        reply_markup=admin_menu_kb(node_id),
    )


# --- Plan Addon Configuration Handlers ---

async def _addon_pricing_caption(plan: Plan, addon_type: str) -> str:
    from bot.config import get_settings
    settings = get_settings()
    mode = getattr(plan, f"extra_{addon_type}_mode", "flexible")
    unit = "GB" if addon_type == "gb" else "Day"
    
    lines = [
        f"⚙️ <b>Plan: {plan.title}</b>",
        f"Type: <b>Extra {addon_type.upper()}</b>",
        f"Current Mode: <b>{mode.capitalize()}</b>",
        ""
    ]
    
    if mode == "flexible":
        fiat = getattr(plan, f"extra_{addon_type}_price_fiat")
        stars = getattr(plan, f"extra_{addon_type}_price_stars")
        usd = getattr(plan, f"extra_{addon_type}_price_usd")
        
        fiat_str = f"{int(fiat):,} {settings.fiat_currency}" if fiat else f"Default ({int(settings.extra_gb_price_fiat if addon_type == 'gb' else settings.extra_time_price_fiat):,} {settings.fiat_currency})"
        stars_str = f"{stars} Stars" if stars else f"Default ({settings.extra_gb_price_stars if addon_type == 'gb' else settings.extra_time_price_stars} Stars)"
        usd_str = f"${usd:g}" if usd else f"Default (${settings.extra_gb_price_usd if addon_type == 'gb' else settings.extra_time_price_usd:g})"
        
        lines.extend([
            f"💳 Price per {unit} (Card): <b>{fiat_str}</b>",
            f"⭐ Price per {unit} (Stars): <b>{stars_str}</b>",
            f"🪙 Price per {unit} (USD): <b>{usd_str}</b>",
            "",
            "ℹ️ In Flexible mode, users can enter any custom quantity, and price is calculated linearly based on these unit rates."
        ])
    else:
        packages = getattr(plan, f"extra_{addon_type}_packages", []) or []
        lines.append("📦 <b>Predefined Packages:</b>")
        if not packages:
            lines.append("  <i>None configured yet. Users won't be able to buy addons!</i>")
        else:
            for pkg in packages:
                val = pkg.get("gb" if addon_type == "gb" else "days")
                fiat = pkg.get("price_fiat")
                stars = pkg.get("price_stars")
                usd = pkg.get("price_usd")
                lines.append(f"  • +{val} {unit} — {int(fiat):,} {settings.fiat_currency} | ⭐ {stars} | ${usd:g}")
                
        lines.extend([
            "",
            f"ℹ️ In Strict mode, users can only choose from these specific packages."
        ])
        
    return "\n".join(lines)


@router.callback_query(F.data.startswith("adm:pextra:"))
async def plan_addon_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    parts = call.data.split(":")
    
    if parts[2] == "mode":
        plan_id = int(parts[3])
        addon_type = parts[4]
        plan = await repo.get_plan(plan_id)
        if not plan:
            await call.answer("Plan not found.", show_alert=True)
            return
            
        cur_mode = getattr(plan, f"extra_{addon_type}_mode", "flexible")
        new_mode = "strict" if cur_mode == "flexible" else "flexible"
        await repo.update_plan(plan_id, **{f"extra_{addon_type}_mode": new_mode})
        plan = await repo.get_plan(plan_id)
        
        from aiogram.exceptions import TelegramBadRequest
        try:
            await call.message.edit_text(
                await _addon_pricing_caption(plan, addon_type),
                reply_markup=plan_addon_pricing_kb(plan, addon_type)
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
        await call.answer(f"Switched to {new_mode} mode.")
        return
        
    if parts[2] == "unit":
        plan_id = int(parts[3])
        addon_type = parts[4]
        field = parts[5]
        
        await state.update_data(plan_id=plan_id, addon_type=addon_type, field=field)
        await state.set_state(EditPlanForm.addon_value)
        
        unit = "GB" if addon_type == "gb" else "Day"
        await call.message.answer(
            f"📝 Send the new price per <b>1 {unit}</b> for <b>{field.upper()}</b> (or 0/empty to clear to fallback):\n"
            "Or type /cancel to cancel."
        )
        await call.answer()
        return
        
    if parts[2] == "pkgs":
        plan_id = int(parts[3])
        addon_type = parts[4]
        
        await state.update_data(plan_id=plan_id, addon_type=addon_type)
        await state.set_state(EditPlanForm.addon_pkgs)
        
        unit = "GB" if addon_type == "gb" else "Days"
        await call.message.answer(
            f"📦 Send the predefined packages for **{addon_type.upper()}**.\n"
            "Each package must be on a new line in the format:\n"
            f"<code>quantity_{unit}:price_fiat:price_stars:price_usd</code>\n\n"
            "Example:\n"
            "<code>10:50000:5:1.5</code>\n"
            "<code>50:200000:20:5.0</code>\n\n"
            "Or send <code>-</code> to clear all packages. Type /cancel to abort."
        )
        await call.answer()
        return

    plan_id = int(parts[2])
    addon_type = parts[3]
    plan = await repo.get_plan(plan_id)
    if not plan:
        await call.answer("Plan not found.", show_alert=True)
        return
        
    await call.message.edit_text(
        await _addon_pricing_caption(plan, addon_type),
        reply_markup=plan_addon_pricing_kb(plan, addon_type)
    )
    await call.answer()


@router.message(EditPlanForm.addon_value)
async def process_addon_unit_price(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    data = await state.get_data()
    plan_id = data.get("plan_id")
    addon_type = data.get("addon_type")
    field = data.get("field")
    
    if text.lower() == "/cancel":
        await state.clear()
        plan = await repo.get_plan(plan_id)
        await message.answer("❌ Cancelled.")
        await message.answer(
            await _addon_pricing_caption(plan, addon_type),
            reply_markup=plan_addon_pricing_kb(plan, addon_type)
        )
        return
        
    try:
        val = float(text)
        if val < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Invalid amount. Please enter a positive number:")
        return
        
    await state.clear()
    
    attr = f"extra_{addon_type}_price_{field}"
    saved_val = int(val) if field == "stars" else val
    if saved_val == 0:
        saved_val = None
        
    plan = await repo.update_plan(plan_id, **{attr: saved_val})
    await message.answer("✅ Unit price updated.")
    
    await message.answer(
        await _addon_pricing_caption(plan, addon_type),
        reply_markup=plan_addon_pricing_kb(plan, addon_type)
    )


@router.message(EditPlanForm.addon_pkgs)
async def process_addon_packages(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    data = await state.get_data()
    plan_id = data.get("plan_id")
    addon_type = data.get("addon_type")
    
    if text.lower() == "/cancel":
        await state.clear()
        plan = await repo.get_plan(plan_id)
        await message.answer("❌ Cancelled.")
        await message.answer(
            await _addon_pricing_caption(plan, addon_type),
            reply_markup=plan_addon_pricing_kb(plan, addon_type)
        )
        return
        
    if text == "-":
        await state.clear()
        plan = await repo.update_plan(plan_id, **{f"extra_{addon_type}_packages": []})
        await message.answer("✅ All packages cleared.")
    else:
        # Parse packages
        lines = text.split("\n")
        packages = []
        for line in lines:
            if not line.strip():
                continue
            parts = line.replace(" ", "").split(":")
            if len(parts) != 4:
                await message.answer("❌ Invalid format. Please enter packages exactly as shown in the example:")
                return
            try:
                qty = float(parts[0])
                fiat = float(parts[1])
                stars = int(parts[2])
                usd = float(parts[3])
                packages.append({
                    "gb" if addon_type == "gb" else "days": qty,
                    "price_fiat": fiat,
                    "price_stars": stars,
                    "price_usd": usd
                })
            except ValueError:
                await message.answer("❌ Numbers could not be parsed. Make sure they are correct:")
                return
                
        await state.clear()
        plan = await repo.update_plan(plan_id, **{f"extra_{addon_type}_packages": packages})
        await message.answer("✅ Packages configured successfully.")
        
    await message.answer(
        await _addon_pricing_caption(plan, addon_type),
        reply_markup=plan_addon_pricing_kb(plan, addon_type)
    )

