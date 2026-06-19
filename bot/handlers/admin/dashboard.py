from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.db import repo
from bot.filters import IsAdmin
from bot.keyboards.admin_kb import admin_menu_kb, admin_settings_kb
from bot.panel.client import PanelError, get_panel_client
from bot.states.forms import BackupSettingsForm, SettingsForm
from aiogram.types import InlineKeyboardMarkup
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.web.auth import generate_login_token

from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)
router = Router(name="admin-dashboard")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.message(Command("webpanel"))
async def cmd_webpanel(message: Message) -> None:
    settings = get_settings()
    base_url = settings.public_base_url
    if not base_url:
        await message.answer(
            "⚠️ <code>public_base_url</code> is not configured.\n"
            "Please configure it in settings or .env first (e.g. settings -> Public Base URL)."
        )
        return
    
    login_token = generate_login_token(message.from_user.id)
    url = f"{base_url}/admin/index.html?token={login_token}"
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🌐 Open Admin Web-UI", url=url)
    
    try:
        await message.answer(
            f"🌐 <b>Web UI Admin Panel</b>\n\n"
            f"Click the button below to log in securely. This link is valid for 5 minutes:\n\n"
            f"<i>Do not share this link with anyone!</i>",
            reply_markup=builder.as_markup()
        )
    except TelegramBadRequest as exc:
        if "url" in str(exc).lower():
            # Fallback for localhost / local IPs which Telegram blocks as button URLs
            await message.answer(
                f"🌐 <b>Web UI Admin Panel</b>\n\n"
                f"Your <code>public_base_url</code> contains a local IP/domain (<code>{base_url}</code>) which Telegram blocks as a button link.\n\n"
                f"Use this text link instead to access the panel:\n"
                f"🔗 <a href=\"{url}\">Open Admin Web-UI Panel</a>\n\n"
                f"<i>Do not share this link with anyone!</i>"
            )
        else:
            raise


@router.callback_query(F.data == "adm:webui")
async def webui_callback(call: CallbackQuery) -> None:
    settings = get_settings()
    base_url = settings.public_base_url
    if not base_url:
        await call.answer("public_base_url is not configured!", show_alert=True)
        return
        
    login_token = generate_login_token(call.from_user.id)
    url = f"{base_url}/admin/index.html?token={login_token}"
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🌐 Open Admin Web-UI", url=url)
    
    try:
        await call.message.answer(
            f"🌐 <b>Web UI Admin Panel</b>\n\n"
            f"Click the button below to log in securely. This link is valid for 5 minutes:\n\n"
            f"<i>Do not share this link with anyone!</i>",
            reply_markup=builder.as_markup()
        )
    except TelegramBadRequest as exc:
        if "url" in str(exc).lower():
            # Fallback for localhost / local IPs which Telegram blocks as button URLs
            await call.message.answer(
                f"🌐 <b>Web UI Admin Panel</b>\n\n"
                f"Your <code>public_base_url</code> contains a local IP/domain (<code>{base_url}</code>) which Telegram blocks as a button link.\n\n"
                f"Use this text link instead to access the panel:\n"
                f"🔗 <a href=\"{url}\">Open Admin Web-UI Panel</a>\n\n"
                f"<i>Do not share this link with anyone!</i>"
            )
        else:
            raise
    await call.answer()


@router.message(F.text.in_(["🛠 Admin Panel", "🛠 پنل مدیریت"]))
async def admin_panel(message: Message, state: FSMContext) -> None:
    await state.clear()
    node_id = getattr(message.bot, "node_id", 0)
    await message.answer("🛠 <b>Admin Panel</b>", reply_markup=admin_menu_kb(node_id))


@router.callback_query(F.data == "adm:menu")
async def admin_menu_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    node_id = getattr(call.bot, "node_id", 0)
    await call.message.edit_text("🛠 <b>Admin Panel</b>", reply_markup=admin_menu_kb(node_id))
    await call.answer()


async def _panel_status_lines() -> str:
    panels = await repo.list_panels(only_active=True)
    if not panels:
        return (
            "🖥 <b>Panels:</b> none configured\n"
            "⚠️ Add a panel in <b>Panels</b> before creating plans."
        )
    lines = ["🖥 <b>Panels:</b>"]
    for panel in panels[:8]:
        try:
            client = await get_panel_client(panel.id)
            status = await client.server_status()
            cpu = status.get("cpu")
            xray = (status.get("xray") or {}).get("state", "?")
            lines.append(f"  • {panel.name}: 🟢 CPU {cpu if cpu is not None else '?'}%, xray {xray}")
        except PanelError as exc:
            lines.append(f"  • {panel.name}: 🔴 {exc}")
    if len(panels) > 8:
        lines.append(f"  … and {len(panels) - 8} more")
    return "\n".join(lines)


@router.callback_query(F.data == "adm:dash")
async def dashboard_cb(call: CallbackQuery) -> None:
    await call.answer("Loading…")
    node_id = getattr(call.bot, "node_id", 0)
    users = await repo.count_users(node_id)
    active = await repo.count_active_services(node_id)
    pending = await repo.count_pending_review_orders(node_id)
    
    if node_id == 0:
        panel_block = "\n\n" + await _panel_status_lines()
    else:
        panel_block = ""

    text = (
        "📊 <b>Dashboard</b>\n\n"
        f"👥 Users: <b>{users}</b>\n"
        f"🛡 Active services: <b>{active}</b>\n"
        f"🧾 Pending receipts: <b>{pending}</b>"
        f"{panel_block}"
    )
    await call.message.edit_text(text, reply_markup=admin_menu_kb(node_id))


async def get_settings_text_and_kb() -> tuple[str, InlineKeyboardMarkup]:
    s = get_settings()
    interval = await repo.get_setting("backup_interval", "24h")
    last_backup_str = await repo.get_setting("last_backup_time", "0")
    try:
        last_backup_time_val = float(last_backup_str)
        if last_backup_time_val > 0:
            import datetime
            last_backup_formatted = datetime.datetime.fromtimestamp(
                last_backup_time_val, datetime.timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            last_backup_formatted = "Never"
    except (ValueError, TypeError):
        last_backup_formatted = "Never"

    text = (
        "⚙️ <b>Settings</b>\n\n"
        f"Brand Name: <b>{s.brand_name}</b>\n"
        f"Support Contact: <b>{s.support_contact}</b>\n"
        f"Fiat Currency: <b>{s.fiat_currency}</b>\n"
        f"Card Number: <code>{s.card_number or '—'}</code>\n"
        f"Card Holder: <b>{s.card_holder or '—'}</b>\n"
        f"Stars Enabled: <b>{'Yes' if s.stars_enabled else 'No'}</b>\n"
        f"Crypto Enabled: <b>{'Yes' if s.crypto_enabled else 'No'}</b>\n"
        f"Admins: <code>{', '.join(str(a) for a in s.admin_ids)}</code>\n\n"
        "💳 <b>NowPayments API Config</b>:\n"
        f"API Key: <code>{s.nowpayments_api_key or '—'}</code>\n"
        f"IPN Secret: <code>{s.nowpayments_ipn_secret or '—'}</code>\n"
        f"Public Base URL: <code>{s.public_base_url or '—'}</code>\n\n"
        "📦 <b>Extra GB Pricing</b>:\n"
        f"Fiat: <b>{int(s.extra_gb_price_fiat):,} {s.fiat_currency}</b>\n"
        f"Stars: <b>{s.extra_gb_price_stars} Stars</b>\n"
        f"USD/Crypto: <b>${s.extra_gb_price_usd:g}</b>\n\n"
        "⏳ <b>Extra Time Pricing</b>:\n"
        f"Fiat: <b>{int(s.extra_time_price_fiat):,} {s.fiat_currency} / Day</b>\n"
        f"Stars: <b>{s.extra_time_price_stars} Stars / Day</b>\n"
        f"USD/Crypto: <b>${s.extra_time_price_usd:g} / Day</b>\n\n"
        "💾 <b>Backup Settings</b>:\n"
        f"Auto Backup Interval: <code>{interval}</code> (0 to disable)\n"
        f"Last Backup: <code>{last_backup_formatted}</code>\n\n"
        "Click a button below to edit any settings dynamically."
    )
    kb = admin_settings_kb(stars_enabled=s.stars_enabled, crypto_enabled=s.crypto_enabled)
    return text, kb


@router.callback_query(F.data == "adm:settings")
async def settings_cb(call: CallbackQuery) -> None:
    text, kb = await get_settings_text_and_kb()
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("adm:set_cfg:"))
async def edit_setting_cb(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":", 2)[2]
    await state.update_data(editing_key=key)
    await state.set_state(SettingsForm.value)

    friendly_names = {
        "brand_name": "Brand Name",
        "support_contact": "Support Contact",
        "fiat_currency": "Fiat Currency (e.g. IRR, USD)",
        "card_number": "Card Number",
        "card_holder": "Card Holder Name",
        "admin_ids_raw": "Admin IDs (comma-separated integers)",
        "nowpayments_api_key": "NowPayments API Key",
        "nowpayments_ipn_secret": "NowPayments IPN Secret",
        "public_base_url": "Public Base URL",
        "extra_gb_price_fiat": "Price per Extra GB (Fiat)",
        "extra_gb_price_stars": "Price per Extra GB (Stars)",
        "extra_gb_price_usd": "Price per Extra GB (USD)",
        "extra_time_price_fiat": "Price per Extra Day (Fiat)",
        "extra_time_price_stars": "Price per Extra Day (Stars)",
        "extra_time_price_usd": "Price per Extra Day (USD)",
    }
    name = friendly_names.get(key, key)
    await call.message.answer(
        f"📝 Please enter the new value for <b>{name}</b>:\n"
        "Or type /cancel to cancel editing."
    )
    await call.answer()


@router.message(SettingsForm.value)
async def process_setting_value(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if text.lower() == "/cancel":
        await state.clear()
        text_msg, kb = await get_settings_text_and_kb()
        await message.answer("❌ Editing cancelled.\n\n" + text_msg, reply_markup=kb)
        return

    data = await state.get_data()
    key = data.get("editing_key")
    if not key:
        await state.clear()
        return

    # Validation & lockout prevention
    if key == "admin_ids_raw":
        parts = text.replace(" ", "").split(",")
        valid = True
        parsed_ids = []
        for p in parts:
            if p:
                try:
                    parsed_ids.append(int(p))
                except ValueError:
                    valid = False
                    break
        if not valid or not parsed_ids:
            await message.answer("❌ Invalid format. Please enter comma-separated numeric IDs (e.g., 123456,789101):")
            return

        # Lockout prevention
        if message.from_user.id not in parsed_ids:
            await message.answer(
                f"❌ <b>Lockout Prevention Guard</b>: Your own Telegram ID (<code>{message.from_user.id}</code>) "
                "must be included in the Admin list. Please re-enter the list including your ID:"
            )
            return

    # Update memory settings singleton
    s = get_settings()
    if key in ("extra_gb_price_stars", "extra_time_price_stars"):
        try:
            val = int(text)
            if val < 0:
                raise ValueError
        except ValueError:
            await message.answer("❌ Invalid number. Please enter a positive integer:")
            return
        await repo.set_setting(key, text)
        setattr(s, key, val)
    elif key in ("extra_gb_price_fiat", "extra_gb_price_usd", "extra_time_price_fiat", "extra_time_price_usd"):
        try:
            val = float(text)
            if val < 0:
                raise ValueError
        except ValueError:
            await message.answer("❌ Invalid number. Please enter a positive number:")
            return
        await repo.set_setting(key, text)
        setattr(s, key, val)
    else:
        await repo.set_setting(key, text)
        setattr(s, key, text)

    await state.clear()
    text_msg, kb = await get_settings_text_and_kb()
    await message.answer(f"✅ Setting <b>{key}</b> has been updated.\n\n" + text_msg, reply_markup=kb)


@router.callback_query(F.data.startswith("adm:set_cfg_toggle:"))
async def toggle_setting_cb(call: CallbackQuery) -> None:
    key = call.data.split(":", 2)[2]
    s = get_settings()
    current_val = getattr(s, key, False)
    new_val = not current_val

    # Save to database
    await repo.set_setting(key, "true" if new_val else "false")
    # Update memory settings singleton
    setattr(s, key, new_val)

    await call.answer(f"Toggled {key} to {new_val}")

    text_msg, kb = await get_settings_text_and_kb()
    try:
        await call.message.edit_text(text_msg, reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data == "adm:set_backup_interval")
async def edit_backup_interval_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BackupSettingsForm.interval)
    await call.message.answer(
        "📝 Please enter the backup interval as a duration string with units\n"
        "(e.g., <code>30m</code>, <code>12h</code>, <code>1d</code>), or <code>0</code> to disable auto backups:"
    )
    await call.answer()


@router.message(BackupSettingsForm.interval)
async def process_backup_interval(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    try:
        from bot.services.backup import parse_duration_to_seconds
        parse_duration_to_seconds(text)  # validates format

        await repo.set_setting("backup_interval", text)
        await state.clear()

        text_msg, kb = await get_settings_text_and_kb()
        await message.answer(f"✅ Backup interval updated to: <code>{text}</code>\n\n" + text_msg, reply_markup=kb)
    except ValueError:
        await message.answer(
            "❌ Invalid format. Please enter a duration like <code>30m</code>, <code>12h</code>, <code>1d</code>, or <code>0</code> to disable:"
        )


@router.callback_query(F.data == "adm:backup_now")
async def backup_now_cb(call: CallbackQuery) -> None:
    await call.answer("Creating backup…")
    from bot.services.backup import perform_backup

    success = await perform_backup(call.bot, target_chat_id=call.from_user.id)
    if success:
        import time
        await repo.set_setting("last_backup_time", str(time.time()))
        await call.message.answer("✅ Backup completed successfully and sent to you!")

        text_msg, kb = await get_settings_text_and_kb()
        try:
            await call.message.edit_text(text_msg, reply_markup=kb)
        except Exception:
            pass
    else:
        await call.message.answer("❌ Backup failed. Please check logs.")


class NodeSettingsForm(StatesGroup):
    value = State()


@router.callback_query(F.data == "node:settings")
async def node_settings_cb(call: CallbackQuery) -> None:
    node_id = getattr(call.bot, "node_id", 0)
    if node_id == 0:
        await call.answer("This setting is only available on Node Bots.")
        return

    node = await repo.get_node(node_id)
    if not node:
        await call.answer("Node not found.", show_alert=True)
        return

    text = (
        f"⚙️ <b>Node Settings</b>\n\n"
        f"Brand Name: <b>{node.brand_name}</b>\n"
        f"Support Contact: <b>{node.support_contact}</b>\n"
        f"Card Number: <code>{node.card_number or '—'}</code>\n"
        f"Card Holder: <b>{node.card_holder or '—'}</b>\n\n"
        "Use the buttons below to change any values directly on this bot."
    )

    kb = InlineKeyboardBuilder()
    kb.button(text="Brand Name", callback_data="nodeset:edit:brand_name")
    kb.button(text="Support Contact", callback_data="nodeset:edit:support_contact")
    kb.button(text="Card Number", callback_data="nodeset:edit:card_number")
    kb.button(text="Card Holder", callback_data="nodeset:edit:card_holder")
    kb.button(text="⬅️ Back", callback_data="adm:menu")
    kb.adjust(2)

    await call.message.edit_text(text, reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("nodeset:edit:"))
async def node_edit_setting_start(call: CallbackQuery, state: FSMContext) -> None:
    field = call.data.split(":")[2]
    await state.update_data(editing_field=field)
    await state.set_state(NodeSettingsForm.value)

    names = {
        "brand_name": "Brand Name",
        "support_contact": "Support Contact",
        "card_number": "Card Number",
        "card_holder": "Card Holder Name"
    }
    await call.message.answer(f"📝 Send the new value for <b>{names[field]}</b>:")
    await call.answer()


@router.message(NodeSettingsForm.value)
async def process_node_setting_val(message: Message, state: FSMContext) -> None:
    val = message.text.strip()
    data = await state.get_data()
    field = data.get("editing_field")
    node_id = getattr(message.bot, "node_id", 0)
    await state.clear()

    if node_id > 0:
        await repo.update_node(node_id, **{field: val})
        updated = await repo.get_node(node_id)
        from bot.config import update_node_cache
        update_node_cache(
            node_id=node_id,
            owner_tg_id=updated.owner_tg_id,
            brand_name=updated.brand_name,
            support_contact=updated.support_contact,
            card_number=updated.card_number,
            card_holder=updated.card_holder,
        )
        await message.answer(f"✅ Setting `{field}` updated successfully to: <b>{val}</b>", reply_markup=admin_menu_kb(node_id))
    else:
        await message.answer("❌ Error: Not in a Node Bot.")



