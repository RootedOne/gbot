from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import repo
from bot.db.repo import PanelDeleteError
from bot.filters import IsAdmin
from bot.keyboards.admin_kb import (
    admin_menu_kb,
    admin_panel_detail_kb,
    admin_panels_kb,
    confirm_kb,
    inbound_picker_kb,
    panel_edit_menu_kb,
)
from bot.panel.client import PanelError, get_panel_client
from bot.states.forms import EditPanelForm, PanelForm
from bot.utils.format import human_bytes

logger = logging.getLogger(__name__)
router = Router(name="admin-panels")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_EDIT_FIELDS = {
    "name": ("name", "Send the new <b>panel name</b>:", "text"),
    "base_url": ("base_url", "Send the new <b>base URL</b> (no trailing slash):", "url"),
    "token": ("api_token", "Send the new <b>API token</b>:", "text"),
    "verify_tls": (
        "verify_tls",
        "Send <b>TLS verify</b>: <code>true</code> or <code>false</code>:",
        "bool",
    ),
    "sub_base_url": (
        "sub_base_url",
        "Send the <b>subscription base URL(s)</b> (comma-separated for multiple, or <code>-</code> to use base URL):",
        "opt_url",
    ),
    "reseller_gb_price": (
        "reseller_gb_price",
        "Send the **Reseller GB Price** for this server (in tomans, or <code>0</code> to use global reseller GB price):",
        "float",
    ),
    "reseller_unlimited_price": (
        "reseller_unlimited_price",
        "Send the **Reseller Unlimited Plan Price** for this server (in tomans, or <code>0</code> to use global reseller unlimited price):",
        "float",
    ),
    "middle_server_url": (
        "middle_server_url",
        "Send the new <b>Middle Server URL</b> (no trailing slash):",
        "url",
    ),
    "middle_server_token": (
        "middle_server_token",
        "Send the new <b>Middle Server authorization token</b>:",
        "text",
    ),
}


def _mask_token(token: str) -> str:
    if len(token) <= 4:
        return "••••"
    return f"••••{token[-4:]}"


def _panel_caption(panel) -> str:
    migr_inb = list(panel.migration_inbound_ids or [])
    res_inb = list(panel.reseller_inbound_ids or [])
    res_price = getattr(panel, "reseller_gb_price", 0.0)
    res_price_str = f"{int(res_price):,} tomans/GB" if res_price > 0 else "Default reseller price"
    res_unl_price = getattr(panel, "reseller_unlimited_price", 0.0)
    res_unl_price_str = f"{int(res_unl_price):,} tomans" if res_unl_price > 0 else "Default reseller price"
    caption = (
        f"🖥 <b>{panel.name}</b>\n"
        f"Status: {'🟢 active' if panel.is_active else '⚪️ inactive'}\n"
        f"URL: <code>{panel.base_url}</code>\n"
        f"Token: <code>{_mask_token(panel.api_token)}</code>\n"
        f"TLS verify: {panel.verify_tls}\n"
        f"Sub base: <code>{panel.sub_base_url or panel.base_url}</code>\n"
        f"Migrations: {'✅ allowed' if panel.allow_migrations else '❌ disabled'}\n"
        f"Migration inbounds: {migr_inb or '—'}\n"
        f"Trials: {'✅ allowed' if panel.allow_trials else '❌ disabled'}\n"
        f"Trial inbounds: {panel.trial_inbound_ids or '—'}\n"
        f"Resellers: {'✅ allowed' if panel.allow_resellers else '❌ disabled'}\n"
        f"Reseller inbounds: {res_inb or '—'}\n"
        f"Reseller GB Price: <b>{res_price_str}</b>\n"
        f"Reseller Unlimited Price: <b>{res_unl_price_str}</b>\n"
        f"Middle Server: <b>{'🟢 enabled' if getattr(panel, 'use_middle_server', False) else '❌ disabled'}</b>"
    )
    if getattr(panel, "use_middle_server", False):
        res_url = getattr(panel, "middle_server_url", "")
        res_tok = getattr(panel, "middle_server_token", "")
        return (
            caption + "\n"
            f"Middle Server URL: <code>{res_url}</code>\n"
            f"Middle Server Token: <code>{_mask_token(res_tok)}</code>"
        )
    return caption


async def _panel_detail_text(panel) -> str:
    plan_count, service_count = await repo.count_panel_links(panel.id)
    orphan = await repo.count_orphan_services_for_panel(panel.id)
    text = _panel_caption(panel)
    text += (
        f"\n\n📎 Linked plans: <b>{plan_count}</b>\n"
        f"📎 Linked services: <b>{service_count}</b>"
    )
    if orphan:
        text += f"\n⚠️ Orphan services (need backfill): <b>{orphan}</b>"
    return text


async def _show_panel_detail(message: Message, panel_id: int) -> None:
    panel = await repo.get_panel(panel_id)
    if panel is None:
        await message.answer("Panel not found.")
        return
    orphan = await repo.count_orphan_services_for_panel(panel.id)
    await message.answer(
        await _panel_detail_text(panel),
        reply_markup=admin_panel_detail_kb(panel, orphan_count=orphan),
    )


# ----------------------------- listing -----------------------------

@router.callback_query(F.data == "adm:panels")
async def panels_list(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    panels = await repo.list_panels()
    if not panels:
        text = (
            "🖥 <b>Panels</b>\n\n"
            "No panels configured yet. Add your first 3X-UI panel to start "
            "creating plans and provisioning VPNs."
        )
    else:
        text = "🖥 <b>Panels</b>"
    await call.message.edit_text(text, reply_markup=admin_panels_kb(panels))
    await call.answer()


@router.callback_query(F.data.regexp(r"^adm:panel:\d+$"))
async def panel_detail(call: CallbackQuery) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    panel = await repo.get_panel(panel_id)
    if panel is None:
        await call.answer("Not found.", show_alert=True)
        return
    orphan = await repo.count_orphan_services_for_panel(panel.id)
    await call.message.edit_text(
        await _panel_detail_text(panel),
        reply_markup=admin_panel_detail_kb(panel, orphan_count=orphan),
    )
    await call.answer()


# ----------------------------- create FSM -----------------------------

@router.callback_query(F.data == "adm:panel:new")
async def panel_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PanelForm.name)
    await call.message.answer(
        "🆕 <b>New panel</b>\n\nSend a short <b>display name</b> (e.g. EU Server 1):"
    )
    await call.answer()


@router.message(PanelForm.name)
async def panel_form_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Please send a non-empty name.")
        return
    await state.update_data(name=name)
    await state.set_state(PanelForm.base_url)
    await message.answer(
        "Send the panel <b>base URL</b> (include path if any, no trailing slash):\n"
        "e.g. <code>https://panel.example.com:54321/secret</code>"
    )


@router.message(PanelForm.base_url)
async def panel_form_url(message: Message, state: FSMContext) -> None:
    url = (message.text or "").strip().rstrip("/")
    if not url.startswith("http"):
        await message.answer("Please send a valid http(s) URL.")
        return
    await state.update_data(base_url=url)
    await state.set_state(PanelForm.token)
    await message.answer("Send the panel <b>API token</b> (Bearer):")


@router.message(PanelForm.token)
async def panel_form_token(message: Message, state: FSMContext) -> None:
    token = (message.text or "").strip()
    if not token:
        await message.answer("Please send a non-empty token.")
        return
    await state.update_data(token=token)
    await state.set_state(PanelForm.verify_tls)
    await message.answer(
        "Verify TLS certificate? Send <code>true</code> or <code>false</code>\n"
        "(use <code>false</code> only for trusted self-signed certs):"
    )


@router.message(PanelForm.verify_tls)
async def panel_form_tls(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().lower()
    verify = raw not in ("false", "0", "no", "off")
    await state.update_data(verify_tls=verify)
    await state.set_state(PanelForm.sub_base_url)
    await message.answer(
        "Send the public <b>subscription base URL(s)</b> (comma-separated for multiple, "
        "or <code>-</code> to use the panel base URL):"
    )


@router.message(PanelForm.sub_base_url)
async def panel_form_sub(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw in ("", "-"):
        sub_base = ""
    else:
        sub_base = ",".join([u.strip().rstrip("/") for u in raw.replace("\n", ",").split(",") if u.strip()])
    await state.update_data(sub_base_url=sub_base)
    await state.set_state(PanelForm.use_middle_server)
    await message.answer(
        "Use Middle Server for this panel? Send <code>true</code> or <code>false</code>:\n"
        "(If enabled, the bot's API calls will route through this relay server)"
    )


@router.message(PanelForm.use_middle_server)
async def panel_form_use_middle_server(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().lower()
    use_mid = raw in ("true", "1", "yes", "on")
    await state.update_data(use_middle_server=use_mid)
    if use_mid:
        await state.set_state(PanelForm.middle_server_url)
        await message.answer(
            "Send the <b>Middle Server URL</b> (include path if any, no trailing slash):\n"
            "e.g. <code>http://middle-server-ip:8000</code>"
        )
    else:
        data = await state.get_data()
        await state.clear()
        panel = await repo.create_panel(
            name=data["name"],
            base_url=data["base_url"],
            api_token=data["token"],
            verify_tls=data.get("verify_tls", True),
            sub_base_url=data.get("sub_base_url", ""),
            use_middle_server=False,
            middle_server_url="",
            middle_server_token="",
            is_active=True,
        )
        await message.answer(
            f"✅ Panel <b>{panel.name}</b> created.\n\n" + _panel_caption(panel),
            reply_markup=admin_panel_detail_kb(panel),
        )


@router.message(PanelForm.middle_server_url)
async def panel_form_middle_server_url(message: Message, state: FSMContext) -> None:
    url = (message.text or "").strip().rstrip("/")
    if not url.startswith("http"):
        await message.answer("Please send a valid http(s) URL.")
        return
    await state.update_data(middle_server_url=url)
    await state.set_state(PanelForm.middle_server_token)
    await message.answer("Send the <b>Middle Server authorization token</b>:")


@router.message(PanelForm.middle_server_token)
async def panel_form_middle_server_token(message: Message, state: FSMContext) -> None:
    token = (message.text or "").strip()
    if not token:
        await message.answer("Please send a non-empty token.")
        return
    data = await state.get_data()
    await state.clear()
    panel = await repo.create_panel(
        name=data["name"],
        base_url=data["base_url"],
        api_token=data["token"],
        verify_tls=data.get("verify_tls", True),
        sub_base_url=data.get("sub_base_url", ""),
        use_middle_server=True,
        middle_server_url=data["middle_server_url"],
        middle_server_token=token,
        is_active=True,
    )
    await message.answer(
        f"✅ Panel <b>{panel.name}</b> created with Middle Server.\n\n" + _panel_caption(panel),
        reply_markup=admin_panel_detail_kb(panel),
    )


# ----------------------------- edit -----------------------------

async def _show_edit_menu(message: Message, panel) -> None:
    await message.answer(
        f"✏️ <b>Edit panel</b>\n\n{_panel_caption(panel)}\n\nChoose a field:",
        reply_markup=panel_edit_menu_kb(panel),
    )


@router.callback_query(F.data.startswith("adm:pnledit:"))
async def panel_edit_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    panel_id = int(call.data.rsplit(":", 1)[1])
    panel = await repo.get_panel(panel_id)
    if panel is None:
        await call.answer("Not found.", show_alert=True)
        return
    await call.message.edit_text(
        f"✏️ <b>Edit panel</b>\n\n{_panel_caption(panel)}\n\nChoose a field:",
        reply_markup=panel_edit_menu_kb(panel),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm:pnlfield:"))
async def panel_edit_field(call: CallbackQuery, state: FSMContext) -> None:
    _, _, panel_id_raw, field = call.data.split(":")
    panel_id = int(panel_id_raw)
    meta = _EDIT_FIELDS.get(field)
    if meta is None:
        await call.answer("Unknown field.", show_alert=True)
        return
    await state.set_state(EditPanelForm.value)
    await state.update_data(panel_id=panel_id, field=field)
    await call.message.answer(meta[1])
    await call.answer()


@router.callback_query(F.data.startswith("adm:panel:migratetoggle:"))
async def panel_migrate_toggle(call: CallbackQuery) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    panel = await repo.get_panel(panel_id)
    if panel is None:
        await call.answer("Not found.", show_alert=True)
        return
    panel = await repo.update_panel(panel_id, allow_migrations=not panel.allow_migrations)
    orphan = await repo.count_orphan_services_for_panel(panel_id)
    await call.message.edit_text(
        await _panel_detail_text(panel),
        reply_markup=admin_panel_detail_kb(panel, orphan_count=orphan),
    )
    await call.answer("Updated.")


@router.callback_query(F.data.startswith("adm:panel:midservertoggle:"))
async def panel_midserver_toggle(call: CallbackQuery) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    panel = await repo.get_panel(panel_id)
    if panel is None:
        await call.answer("Not found.", show_alert=True)
        return
    # Toggle middle server use status
    new_val = not getattr(panel, "use_middle_server", False)
    panel = await repo.update_panel(panel_id, use_middle_server=new_val)
    # Re-draw the edit menu
    await call.message.edit_text(
        f"✏️ <b>Edit panel</b>\n\n{_panel_caption(panel)}\n\nChoose a field:",
        reply_markup=panel_edit_menu_kb(panel),
    )
    await call.answer("Middle Server toggle updated.")


@router.callback_query(F.data.startswith("adm:panel:trialtoggle:"))
async def panel_trial_toggle(call: CallbackQuery) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    panel = await repo.get_panel(panel_id)
    if panel is None:
        await call.answer("Not found.", show_alert=True)
        return
    panel = await repo.update_panel(panel_id, allow_trials=not panel.allow_trials)
    orphan = await repo.count_orphan_services_for_panel(panel_id)
    await call.message.edit_text(
        await _panel_detail_text(panel),
        reply_markup=admin_panel_detail_kb(panel, orphan_count=orphan),
    )
    await call.answer("Updated.")


@router.callback_query(F.data.startswith("adm:panel:resellertoggle:"))
async def panel_reseller_toggle(call: CallbackQuery) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    panel = await repo.get_panel(panel_id)
    if panel is None:
        await call.answer("Not found.", show_alert=True)
        return
    panel = await repo.update_panel(panel_id, allow_resellers=not panel.allow_resellers)
    orphan = await repo.count_orphan_services_for_panel(panel_id)
    await call.message.edit_text(
        await _panel_detail_text(panel),
        reply_markup=admin_panel_detail_kb(panel, orphan_count=orphan),
    )
    await call.answer("Updated.")


@router.callback_query(F.data.startswith("adm:panel:minbounds:"))
async def panel_migration_inbounds_start(call: CallbackQuery, state: FSMContext) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    panel = await repo.get_panel(panel_id)
    if panel is None:
        await call.answer("Not found.", show_alert=True)
        return
    try:
        client = await get_panel_client(panel_id)
        options = await client.inbound_options()
    except PanelError as exc:
        await state.set_state(EditPanelForm.migration_inbounds)
        await state.update_data(
            panel_id=panel_id,
            inbound_ids=list(panel.migration_inbound_ids or []),
        )
        await call.message.answer(
            f"⚠️ Could not load inbounds: {exc}\n"
            "Send inbound IDs manually as comma-separated numbers (e.g. 3,5):"
        )
        await call.answer()
        return

    selected = list(panel.migration_inbound_ids or [])
    await state.set_state(EditPanelForm.migration_inbounds)
    await state.update_data(
        panel_id=panel_id,
        inbound_ids=selected,
        inbound_options=[
            {"id": o.id, "remark": o.remark, "protocol": o.protocol, "port": o.port}
            for o in options
        ],
    )
    await call.message.answer(
        "🔌 Select inbounds for <b>migrated</b> clients on this panel:",
        reply_markup=inbound_picker_kb(options, selected, prefix="adm:minb"),
    )
    await call.answer()


def _inbound_options_from_state(data: dict):
    from bot.panel.schemas import InboundOption

    return [InboundOption.from_api(o) for o in data.get("inbound_options", [])]


@router.callback_query(EditPanelForm.migration_inbounds, F.data.startswith("adm:minb:"))
async def panel_migration_inbound_toggle(call: CallbackQuery, state: FSMContext) -> None:
    action = call.data.rsplit(":", 1)[1]
    data = await state.get_data()
    selected = list(data.get("inbound_ids", []))
    options = _inbound_options_from_state(data)
    panel_id = data.get("panel_id")

    if action == "done":
        if not selected:
            await call.answer("Select at least one inbound.", show_alert=True)
            return
        await state.clear()
        panel = await repo.update_panel(panel_id, migration_inbound_ids=selected)
        await call.message.edit_text(f"✅ Migration inbounds updated: {selected}")
        if panel is not None:
            await _show_edit_menu(call.message, panel)
        await call.answer()
        return

    inbound_id = int(action)
    if inbound_id in selected:
        selected.remove(inbound_id)
    else:
        selected.append(inbound_id)
    await state.update_data(inbound_ids=selected)
    await call.message.edit_reply_markup(
        reply_markup=inbound_picker_kb(options, selected, prefix="adm:minb")
    )
    await call.answer()


@router.message(EditPanelForm.migration_inbounds)
async def panel_migration_inbounds_manual(message: Message, state: FSMContext) -> None:
    ids = [int(p.strip()) for p in (message.text or "").split(",") if p.strip().isdigit()]
    if not ids:
        await message.answer("Please send inbound IDs like: 3,5")
        return
    data = await state.get_data()
    panel_id = data.get("panel_id")
    await state.clear()
    panel = await repo.update_panel(panel_id, migration_inbound_ids=ids)
    if panel is None:
        await message.answer("Panel not found.")
        return
    await message.answer(f"✅ Migration inbounds updated: {ids}")
    await _show_edit_menu(message, panel)


@router.callback_query(F.data.startswith("adm:panel:tinbounds:"))
async def panel_trial_inbounds_start(call: CallbackQuery, state: FSMContext) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    panel = await repo.get_panel(panel_id)
    if panel is None:
        await call.answer("Not found.", show_alert=True)
        return
    try:
        client = await get_panel_client(panel_id)
        options = await client.inbound_options()
    except PanelError as exc:
        await state.set_state(EditPanelForm.trial_inbounds)
        await state.update_data(
            panel_id=panel_id,
            inbound_ids=list(panel.trial_inbound_ids or []),
        )
        await call.message.answer(
            f"⚠️ Could not load inbounds: {exc}\n"
            "Send trial inbound IDs manually as comma-separated numbers (e.g. 3,5):"
        )
        await call.answer()
        return

    selected = list(panel.trial_inbound_ids or [])
    await state.set_state(EditPanelForm.trial_inbounds)
    await state.update_data(
        panel_id=panel_id,
        inbound_ids=selected,
        inbound_options=[
            {"id": o.id, "remark": o.remark, "protocol": o.protocol, "port": o.port}
            for o in options
        ],
    )
    await call.message.answer(
        "🔌 Select inbounds for <b>trial</b> clients on this panel:",
        reply_markup=inbound_picker_kb(options, selected, prefix="adm:tinb"),
    )
    await call.answer()


@router.callback_query(EditPanelForm.trial_inbounds, F.data.startswith("adm:tinb:"))
async def panel_trial_inbound_toggle(call: CallbackQuery, state: FSMContext) -> None:
    action = call.data.rsplit(":", 1)[1]
    data = await state.get_data()
    selected = list(data.get("inbound_ids", []))
    options = _inbound_options_from_state(data)
    panel_id = data.get("panel_id")

    if action == "done":
        if not selected:
            await call.answer("Select at least one inbound.", show_alert=True)
            return
        await state.clear()
        panel = await repo.update_panel(panel_id, trial_inbound_ids=selected)
        await call.message.edit_text(f"✅ Trial inbounds updated: {selected}")
        if panel is not None:
            await _show_edit_menu(call.message, panel)
        await call.answer()
        return

    inbound_id = int(action)
    if inbound_id in selected:
        selected.remove(inbound_id)
    else:
        selected.append(inbound_id)
    await state.update_data(inbound_ids=selected)
    await call.message.edit_reply_markup(
        reply_markup=inbound_picker_kb(options, selected, prefix="adm:tinb")
    )
    await call.answer()


@router.message(EditPanelForm.trial_inbounds)
async def panel_trial_inbounds_manual(message: Message, state: FSMContext) -> None:
    ids = [int(p.strip()) for p in (message.text or "").split(",") if p.strip().isdigit()]
    if not ids:
        await message.answer("Please send inbound IDs like: 3,5")
        return
    data = await state.get_data()
    panel_id = data.get("panel_id")
    await state.clear()
    panel = await repo.update_panel(panel_id, trial_inbound_ids=ids)
    if panel is None:
        await message.answer("Panel not found.")
        return
    await message.answer(f"✅ Trial inbounds updated: {ids}")
    await _show_edit_menu(message, panel)


@router.callback_query(F.data.startswith("adm:panel:resinbounds:"))
async def panel_reseller_inbounds_start(call: CallbackQuery, state: FSMContext) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    panel = await repo.get_panel(panel_id)
    if panel is None:
        await call.answer("Not found.", show_alert=True)
        return
    try:
        client = await get_panel_client(panel_id)
        options = await client.inbound_options()
    except PanelError as exc:
        await state.set_state(EditPanelForm.reseller_inbounds)
        await state.update_data(
            panel_id=panel_id,
            inbound_ids=list(panel.reseller_inbound_ids or []),
        )
        await call.message.answer(
            f"⚠️ Could not load inbounds: {exc}\n"
            "Send reseller inbound IDs manually as comma-separated numbers (e.g. 3,5):"
        )
        await call.answer()
        return

    selected = list(panel.reseller_inbound_ids or [])
    await state.set_state(EditPanelForm.reseller_inbounds)
    await state.update_data(
        panel_id=panel_id,
        inbound_ids=selected,
        inbound_options=[
            {"id": o.id, "remark": o.remark, "protocol": o.protocol, "port": o.port}
            for o in options
        ],
    )
    await call.message.answer(
        "🔌 Select allowed inbounds for <b>resellers</b> on this panel:",
        reply_markup=inbound_picker_kb(options, selected, prefix="adm:resinb"),
    )
    await call.answer()


@router.callback_query(EditPanelForm.reseller_inbounds, F.data.startswith("adm:resinb:"))
async def panel_reseller_inbound_toggle(call: CallbackQuery, state: FSMContext) -> None:
    action = call.data.rsplit(":", 1)[1]
    data = await state.get_data()
    selected = list(data.get("inbound_ids", []))
    options = _inbound_options_from_state(data)
    panel_id = data.get("panel_id")

    if action == "done":
        await state.clear()
        panel = await repo.update_panel(panel_id, reseller_inbound_ids=selected)
        await call.message.edit_text(f"✅ Reseller allowed inbounds updated: {selected}")
        if panel is not None:
            await _show_edit_menu(call.message, panel)
        await call.answer()
        return

    inbound_id = int(action)
    if inbound_id in selected:
        selected.remove(inbound_id)
    else:
        selected.append(inbound_id)
    await state.update_data(inbound_ids=selected)
    await call.message.edit_reply_markup(
        reply_markup=inbound_picker_kb(options, selected, prefix="adm:resinb")
    )
    await call.answer()


@router.message(EditPanelForm.reseller_inbounds)
async def panel_reseller_inbounds_manual(message: Message, state: FSMContext) -> None:
    ids = [int(p.strip()) for p in (message.text or "").split(",") if p.strip().isdigit()]
    if not ids:
        await message.answer("Please send inbound IDs like: 3,5")
        return
    data = await state.get_data()
    panel_id = data.get("panel_id")
    await state.clear()
    panel = await repo.update_panel(panel_id, reseller_inbound_ids=ids)
    if panel is None:
        await message.answer("Panel not found.")
        return
    await message.answer(f"✅ Reseller allowed inbounds updated: {ids}")
    await _show_edit_menu(message, panel)


@router.message(EditPanelForm.value)
async def panel_edit_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data.get("field")
    panel_id = data.get("panel_id")
    meta = _EDIT_FIELDS.get(field)
    if meta is None or panel_id is None:
        await state.clear()
        await message.answer("Edit session expired.")
        return

    attr, _prompt, kind = meta
    raw = (message.text or "").strip()
    if kind == "text":
        if not raw:
            await message.answer("Please send a non-empty value.")
            return
        value = raw
    elif kind == "url":
        value = raw.rstrip("/")
        if not value.startswith("http"):
            await message.answer("Please send a valid http(s) URL.")
            return
    elif kind == "opt_url":
        if raw in ("", "-"):
            value = ""
        else:
            value = ",".join([u.strip().rstrip("/") for u in raw.replace("\n", ",").split(",") if u.strip()])
    elif kind == "bool":
        value = raw.lower() not in ("false", "0", "no", "off")
    elif kind == "float":
        try:
            value = float(raw)
        except ValueError:
            await message.answer("Please enter a valid numeric value:")
            return
    else:
        value = raw

    await state.clear()
    panel = await repo.update_panel(panel_id, **{attr: value})
    if panel is None:
        await message.answer("Panel not found.")
        return
    await message.answer("✅ Updated.")
    await _show_edit_menu(message, panel)


# ----------------------------- actions -----------------------------

@router.callback_query(F.data.startswith("adm:panel:test:"))
async def panel_test(call: CallbackQuery) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    panel = await repo.get_panel(panel_id)
    if panel is None:
        await call.answer("Not found.", show_alert=True)
        return
    await call.answer("Testing…")
    try:
        client = await get_panel_client(panel_id)
        status = await client.server_status()
        cpu = status.get("cpu")
        mem = status.get("mem") or {}
        xray = status.get("xray") or {}
        net = status.get("netIO") or {}
        text = (
            f"🔌 <b>{panel.name}</b> — 🟢 online\n"
            f"  • CPU: {cpu if cpu is not None else '?'}%\n"
            f"  • Mem: {human_bytes(mem.get('current', 0))} / "
            f"{human_bytes(mem.get('total', 0))}\n"
            f"  • Xray: {xray.get('state', '?')}\n"
            f"  • Net: ↑{human_bytes(net.get('up', 0))}/s "
            f"↓{human_bytes(net.get('down', 0))}/s"
        )
    except PanelError as exc:
        text = f"🔌 <b>{panel.name}</b> — 🔴 {exc}"
    await call.message.answer(text)


@router.callback_query(F.data.startswith("adm:panel:toggle:"))
async def panel_toggle(call: CallbackQuery) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    panel = await repo.get_panel(panel_id)
    if panel is None:
        await call.answer("Not found.", show_alert=True)
        return
    panel = await repo.set_panel_active(panel_id, not panel.is_active)
    orphan = await repo.count_orphan_services_for_panel(panel_id)
    await call.message.edit_text(
        await _panel_detail_text(panel),
        reply_markup=admin_panel_detail_kb(panel, orphan_count=orphan),
    )
    await call.answer("Updated.")


@router.callback_query(F.data.startswith("adm:panel:backfill:"))
async def panel_backfill(call: CallbackQuery) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    count = await repo.backfill_service_panel_ids(panel_id)
    panel = await repo.get_panel(panel_id)
    if panel is None:
        await call.answer("Not found.", show_alert=True)
        return
    orphan = await repo.count_orphan_services_for_panel(panel_id)
    await call.message.edit_text(
        await _panel_detail_text(panel),
        reply_markup=admin_panel_detail_kb(panel, orphan_count=orphan),
    )
    await call.answer(f"Backfilled {count} service(s).", show_alert=True)


@router.callback_query(F.data.startswith("adm:panel:del:"))
async def panel_delete_ask(call: CallbackQuery) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    plan_count, service_count = await repo.count_panel_links(panel_id)
    if plan_count or service_count:
        await call.answer(
            f"Cannot delete: {plan_count} plan(s) and {service_count} service(s) "
            "linked. Disable the panel instead.",
            show_alert=True,
        )
        return
    await call.message.edit_text(
        "Delete this panel permanently?",
        reply_markup=confirm_kb(
            f"adm:panel:delok:{panel_id}", f"adm:panel:{panel_id}"
        ),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm:panel:delok:"))
async def panel_delete_confirm(call: CallbackQuery) -> None:
    panel_id = int(call.data.rsplit(":", 1)[1])
    try:
        await repo.delete_panel(panel_id)
    except PanelDeleteError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    panels = await repo.list_panels()
    await call.message.edit_text(
        "🗑 Panel deleted.", reply_markup=admin_panels_kb(panels)
    )
    await call.answer("Deleted.")
