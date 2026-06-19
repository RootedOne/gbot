from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import get_settings
from bot.db import repo
from bot.db.models import PaymentMethod, OrderStatus, ServiceStatus
from bot.db.repo import InsufficientBalance
from bot.filters import IsAdmin
from bot.keyboards.admin_kb import admin_menu_kb
from bot.services import provisioning
from bot.services.delivery import send_configs
from bot.states.forms import AdminUserForm
from bot.utils.format import fmt_expiry, fmt_quota, human_bytes

logger = logging.getLogger(__name__)
router = Router(name="admin-users")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _service_admin_kb(service_id: int, active: bool) -> "InlineKeyboardBuilder":
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Extend", callback_data=f"admsvc:extend:{service_id}")
    if active:
        builder.button(text="⏸ Disable", callback_data=f"admsvc:disable:{service_id}")
    else:
        builder.button(text="▶️ Enable", callback_data=f"admsvc:enable:{service_id}")
    builder.button(text="📋 Configs", callback_data=f"admsvc:configs:{service_id}")
    builder.button(text="🗑 Delete", callback_data=f"admsvc:del:{service_id}")
    builder.adjust(2)
    return builder


@router.callback_query(F.data == "adm:users")
async def users_entry(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="📜 User history (by ID)", callback_data="adm:users:history")
    kb.button(text="🔍 Find service by email", callback_data="adm:users:find")
    kb.button(text="🎁 Grant service to user", callback_data="adm:users:create")
    kb.button(text="💰 Adjust balance", callback_data="adm:users:balance")
    kb.button(text="⬅️ Back", callback_data="adm:menu")
    kb.adjust(1)
    await call.message.edit_text(
        "👤 <b>Manage Users</b>", reply_markup=kb.as_markup()
    )
    await call.answer()


# --------------------------- user history ---------------------------

def _fmt_date(ms: int) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def _history_actions_kb(target: int, is_blocked: bool) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    if is_blocked:
        builder.button(text="✅ Unban", callback_data=f"admuser:unban:{target}")
    else:
        builder.button(text="🚫 Ban", callback_data=f"admuser:ban:{target}")
    builder.button(text="✉️ Whisper", callback_data=f"admuser:whisper:{target}")
    builder.button(text="💰 Update balance", callback_data=f"admuser:balance:{target}")
    builder.button(text="🛡 Manage services", callback_data=f"admuser:svcs:{target}")
    builder.adjust(2)
    return builder


@router.callback_query(F.data == "adm:users:history")
async def history_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminUserForm.history_target)
    await call.message.answer("Send the <b>Telegram user ID</b> to view full history:")
    await call.answer()


@router.message(AdminUserForm.history_target)
async def history_show(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("Please send a numeric Telegram user ID.")
        return
    target = int(text)
    cur = get_settings().fiat_currency

    user = await repo.get_user(target)
    balance = await repo.get_balance(target)
    services = await repo.list_user_services(target, include_deleted=True)
    orders = await repo.list_user_orders(target, limit=15)
    txns = await repo.list_transactions(target, limit=15)

    lines = [f"📜 <b>History for</b> <code>{target}</code>", ""]
    if user is not None:
        uname = f"@{user.username}" if user.username else "—"
        lines.append(f"👤 {user.full_name or '—'} ({uname})")
        lines.append(f"📅 Joined: {_fmt_date(user.created_at)}")
        flags = []
        if user.is_admin:
            flags.append("admin")
        if user.is_blocked:
            flags.append("blocked")
        if flags:
            lines.append(f"🏷 {', '.join(flags)}")
    else:
        lines.append("⚠️ No user record (never used the bot).")
    lines.append(f"💰 <b>Balance:</b> {int(balance):,} {cur}")

    # Services
    lines.append("")
    lines.append(f"🛡 <b>Services ({len(services)}):</b>")
    if services:
        for s in services[:15]:
            quota = "∞" if not s.total_bytes else human_bytes(s.total_bytes)
            lines.append(
                f"• <code>{s.email}</code> — {s.status.value}, "
                f"{quota}, {fmt_expiry(s.expiry_time)}"
            )
    else:
        lines.append("—")

    # Orders
    lines.append("")
    lines.append(f"🧾 <b>Orders ({len(orders)}):</b>")
    if orders:
        for o in orders:
            label = "topup" if o.kind == "topup" else "plan"
            lines.append(
                f"• #{o.id} {label}/{o.method.value} — "
                f"{int(o.amount):,} {o.currency} — {o.status.value} "
                f"({_fmt_date(o.created_at)})"
            )
    else:
        lines.append("—")

    # Transactions
    lines.append("")
    lines.append(f"💳 <b>Wallet transactions ({len(txns)}):</b>")
    if txns:
        for t in txns:
            sign = "➕" if t.amount >= 0 else "➖"
            lines.append(
                f"{sign} {int(abs(t.amount)):,} {cur} — {t.reason} "
                f"({_fmt_date(t.created_at)})"
            )
    else:
        lines.append("—")

    out = "\n".join(lines)
    # Telegram message hard limit is 4096 chars.
    if len(out) > 4096:
        out = out[:4080] + "\n…(truncated)"
    is_blocked = bool(user and user.is_blocked)
    await message.answer(
        out,
        disable_web_page_preview=True,
        reply_markup=_history_actions_kb(target, is_blocked).as_markup(),
    )


# ----------------------- history quick actions -----------------------

@router.callback_query(F.data.startswith("admuser:ban:"))
async def user_ban(call: CallbackQuery, bot: Bot) -> None:
    target = int(call.data.rsplit(":", 1)[1])
    user = await repo.set_user_blocked(target, True)
    if user is None:
        await call.answer("User not found.", show_alert=True)
        return
    await call.answer("User banned.")
    try:
        await call.message.edit_reply_markup(
            reply_markup=_history_actions_kb(target, True).as_markup()
        )
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data.startswith("admuser:unban:"))
async def user_unban(call: CallbackQuery) -> None:
    target = int(call.data.rsplit(":", 1)[1])
    user = await repo.set_user_blocked(target, False)
    if user is None:
        await call.answer("User not found.", show_alert=True)
        return
    await call.answer("User unbanned.")
    try:
        await call.message.edit_reply_markup(
            reply_markup=_history_actions_kb(target, False).as_markup()
        )
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data.startswith("admuser:whisper:"))
async def user_whisper_start(call: CallbackQuery, state: FSMContext) -> None:
    target = int(call.data.rsplit(":", 1)[1])
    await state.set_state(AdminUserForm.whisper_text)
    await state.update_data(target_id=target)
    await call.message.answer(
        f"✉️ Send the message to deliver to <code>{target}</code> "
        "(text, photo, etc.). Send /cancel to abort."
    )
    await call.answer()


@router.message(AdminUserForm.whisper_text, F.text == "/cancel")
async def user_whisper_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Cancelled.")


@router.message(AdminUserForm.whisper_text)
async def user_whisper_send(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    target = data.get("target_id")
    if not target:
        await message.answer("No target. Start again from history.")
        return
    try:
        await bot.send_message(target, "📨 <b>Message from support</b>")
        await bot.copy_message(
            chat_id=target, from_chat_id=message.chat.id, message_id=message.message_id
        )
        await message.answer(f"✅ Delivered to <code>{target}</code>.")
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Could not deliver: {exc}")


@router.callback_query(F.data.startswith("admuser:balance:"))
async def user_balance_quick(call: CallbackQuery, state: FSMContext) -> None:
    target = int(call.data.rsplit(":", 1)[1])
    await state.set_state(AdminUserForm.balance_amount)
    await state.update_data(target_id=target)
    current = await repo.get_balance(target)
    cur = get_settings().fiat_currency
    await call.message.answer(
        f"User <code>{target}</code> balance: <b>{int(current):,} {cur}</b>\n\n"
        "Send the amount to apply: positive to add, negative to remove "
        "(e.g. <code>50000</code> or <code>-20000</code>)."
    )
    await call.answer()


@router.callback_query(F.data.startswith("admuser:svcs:"))
async def user_services_list(call: CallbackQuery) -> None:
    target = int(call.data.rsplit(":", 1)[1])
    services = await repo.list_user_services(target, include_deleted=False)
    if not services:
        await call.answer("No active services for this user.", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    for s in services:
        kb.button(text=f"🛡 {s.email}", callback_data=f"admsvc:open:{s.id}")
    kb.adjust(1)
    await call.message.answer(
        f"🛡 <b>Services of</b> <code>{target}</code>:", reply_markup=kb.as_markup()
    )
    await call.answer()


@router.callback_query(F.data.startswith("admsvc:open:"))
async def admin_service_open(call: CallbackQuery) -> None:
    service = await repo.get_service(int(call.data.rsplit(":", 1)[1]))
    if service is None:
        await call.answer("Not found.", show_alert=True)
        return
    await _show_service(call.message, service)
    await call.answer()


# --------------------------- adjust balance ---------------------------

@router.callback_query(F.data == "adm:users:balance")
async def balance_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminUserForm.balance_target)
    await call.message.answer("Send the <b>Telegram user ID</b> to adjust:")
    await call.answer()


@router.message(AdminUserForm.balance_target)
async def balance_target(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("Please send a numeric Telegram user ID.")
        return
    target = int(text)
    await state.update_data(target_id=target)
    await state.set_state(AdminUserForm.balance_amount)
    current = await repo.get_balance(target)
    cur = get_settings().fiat_currency
    await message.answer(
        f"User <code>{target}</code> balance: <b>{int(current):,} {cur}</b>\n\n"
        "Send the amount to apply: positive to add, negative to remove "
        "(e.g. <code>50000</code> or <code>-20000</code>)."
    )


@router.message(AdminUserForm.balance_amount)
async def balance_amount(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    target = data.get("target_id")
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    try:
        amount = float(raw)
    except ValueError:
        await message.answer("Invalid number. Try again from the menu.")
        return
    if amount == 0:
        await message.answer("Amount can't be zero.")
        return

    cur = get_settings().fiat_currency
    await repo.get_or_create_user(tg_id=target)
    try:
        new_balance = await repo.adjust_balance(
            target,
            amount,
            reason="Admin adjustment",
            admin_id=message.from_user.id,
        )
    except InsufficientBalance:
        current = await repo.get_balance(target)
        await message.answer(
            f"❌ Can't remove {int(abs(amount)):,} {cur}; user only has "
            f"{int(current):,} {cur}."
        )
        return

    verb = "added to" if amount > 0 else "removed from"
    await message.answer(
        f"✅ {int(abs(amount)):,} {cur} {verb} <code>{target}</code>.\n"
        f"New balance: <b>{int(new_balance):,} {cur}</b>."
    )
    try:
        if amount > 0:
            await bot.send_message(
                target,
                f"💰 An admin added <b>{int(amount):,} {cur}</b> to your balance.\n"
                f"New balance: <b>{int(new_balance):,} {cur}</b>.",
            )
        else:
            await bot.send_message(
                target,
                f"💰 An admin adjusted your balance by <b>{int(amount):,} {cur}</b>.\n"
                f"New balance: <b>{int(new_balance):,} {cur}</b>.",
            )
    except Exception:  # noqa: BLE001
        pass


# ----------------------------- find -----------------------------

@router.callback_query(F.data == "adm:users:find")
async def users_find(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminUserForm.find_service)
    await call.message.answer("Send the <b>service email</b> to look up:")
    await call.answer()


@router.message(AdminUserForm.find_service)
async def users_find_input(message: Message, state: FSMContext) -> None:
    await state.clear()
    email = message.text.strip()
    service = await repo.get_service_by_email(email)
    if service is None:
        await message.answer("Not found. Check the email and try again.")
        return
    await _show_service(message, service)


async def _show_service(message: Message, service) -> None:
    traffic = await provisioning.fetch_traffic(service)
    if traffic is not None:
        usage = fmt_quota(traffic.used, traffic.total or service.total_bytes)
        expiry = fmt_expiry(traffic.expiry_time or service.expiry_time)
    else:
        usage = fmt_quota(0, service.total_bytes)
        expiry = fmt_expiry(service.expiry_time)
    active = service.status == ServiceStatus.active
    text = (
        f"🛡 <b>{service.email}</b>\n"
        f"Owner: <code>{service.user_tg_id}</code>\n"
        f"Status: {service.status.value}\n"
        f"📦 {usage}\n"
        f"⏳ {expiry}\n"
        f"🔌 Inbounds: {service.inbound_ids}"
    )
    await message.answer(
        text, reply_markup=_service_admin_kb(service.id, active).as_markup()
    )


@router.callback_query(F.data.startswith("admsvc:disable:"))
async def svc_disable(call: CallbackQuery) -> None:
    service = await repo.get_service(int(call.data.rsplit(":", 1)[1]))
    if service is None:
        await call.answer("Not found.", show_alert=True)
        return
    await provisioning.set_service_enabled(service, False)
    await call.answer("Disabled.")
    await call.message.edit_reply_markup(
        reply_markup=_service_admin_kb(service.id, False).as_markup()
    )


@router.callback_query(F.data.startswith("admsvc:enable:"))
async def svc_enable(call: CallbackQuery) -> None:
    service = await repo.get_service(int(call.data.rsplit(":", 1)[1]))
    if service is None:
        await call.answer("Not found.", show_alert=True)
        return
    await provisioning.set_service_enabled(service, True)
    await call.answer("Enabled.")
    await call.message.edit_reply_markup(
        reply_markup=_service_admin_kb(service.id, True).as_markup()
    )


@router.callback_query(F.data.startswith("admsvc:del:"))
async def svc_del(call: CallbackQuery) -> None:
    service = await repo.get_service(int(call.data.rsplit(":", 1)[1]))
    if service is None:
        await call.answer("Not found.", show_alert=True)
        return
    await provisioning.delete_service(service)
    await call.answer("Deleted.", show_alert=True)
    await call.message.edit_text(f"🗑 Deleted <code>{service.email}</code>.")


@router.callback_query(F.data.startswith("admsvc:configs:"))
async def svc_configs(call: CallbackQuery, bot: Bot) -> None:
    service = await repo.get_service(int(call.data.rsplit(":", 1)[1]))
    if service is None:
        await call.answer("Not found.", show_alert=True)
        return
    await call.answer("Fetching…")
    links, sub_url = await provisioning.fetch_links(service)
    lines = [f"📋 <b>{service.email}</b>", ""]
    if sub_url:
        urls = [u.strip() for u in sub_url.strip().split("\n") if u.strip()]
        if len(urls) > 1:
            for url in urls:
                from urllib.parse import urlparse
                domain = urlparse(url).hostname or url
                lines.append(f"🔗 <b>{domain}</b>:")
                lines.append(f"<code>{url}</code>")
                lines.append("")
        else:
            lines.append(f"🔗 <code>{urls[0]}</code>")
    for link in links[:10]:
        lines.append(f"<code>{link}</code>")
    if not links and not sub_url:
        lines.append("No configs found.")
    await call.message.answer("\n".join(lines), disable_web_page_preview=True)


@router.callback_query(F.data.startswith("admsvc:extend:"))
async def svc_extend(call: CallbackQuery, state: FSMContext) -> None:
    service_id = int(call.data.rsplit(":", 1)[1])
    await state.set_state(AdminUserForm.extend_days)
    await state.update_data(service_id=service_id)
    await call.message.answer(
        "Send the number of <b>days to add</b> (and GB to add, optional, "
        "format: <code>days[,gb]</code> e.g. <code>30,50</code>):"
    )
    await call.answer()


@router.message(AdminUserForm.extend_days)
async def svc_extend_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    service = await repo.get_service(data.get("service_id"))
    if service is None:
        await message.answer("Service not found.")
        return
    parts = [p.strip() for p in (message.text or "").split(",")]
    try:
        days = int(parts[0]) if parts and parts[0] else 0
    except ValueError:
        days = 0
    gb = 0
    if len(parts) > 1 and parts[1]:
        try:
            gb = int(parts[1])
        except ValueError:
            gb = 0

    try:
        result = await provisioning.extend_service(service, add_days=days, add_gb=gb)
        await message.answer(
            f"✅ Extended <code>{service.email}</code> by {days} days"
            + (f" / +{gb} GB" if gb else "")
            + "."
        )
        # notify owner
        try:
            await send_configs(
                message.bot, service.user_tg_id, result, title="Service extended"
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        logger.exception("extend failed: %s", exc)
        await message.answer(f"⚠️ Extend failed: {exc}")


# --------------------------- manual grant ---------------------------

@router.callback_query(F.data == "adm:users:create")
async def grant_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminUserForm.create_target)
    await call.message.answer(
        "Send the <b>Telegram user ID</b> to grant a service to:"
    )
    await call.answer()


@router.message(AdminUserForm.create_target)
async def grant_target(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("Please send a numeric Telegram user ID.")
        return
    await state.update_data(target_id=int(text))
    await state.set_state(AdminUserForm.create_plan)
    plans = await repo.list_plans(only_active=False)
    if not plans:
        await state.clear()
        await message.answer("No plans exist yet. Create one first.")
        return
    kb = InlineKeyboardBuilder()
    for plan in plans:
        kb.button(text=plan.title, callback_data=f"admgrant:{plan.id}")
    kb.adjust(1)
    await message.answer(
        "Choose a plan to grant (free of charge):", reply_markup=kb.as_markup()
    )


@router.callback_query(AdminUserForm.create_plan, F.data.startswith("admgrant:"))
async def grant_plan(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    plan_id = int(call.data.rsplit(":", 1)[1])
    data = await state.get_data()
    await state.clear()
    target_id = data.get("target_id")
    plan = await repo.get_plan(plan_id)
    if plan is None or target_id is None:
        await call.answer("Missing data.", show_alert=True)
        return

    await repo.get_or_create_user(tg_id=target_id)
    order = await repo.create_order(
        user_tg_id=target_id,
        plan_id=plan.id,
        method=PaymentMethod.manual,
        amount=0,
        currency="",
        status=OrderStatus.paid,
    )
    try:
        result = await provisioning.provision_for_plan(
            target_id, plan, order_id=order.id
        )
        await send_configs(bot, target_id, result, title="You received a VPN plan 🎁")
        await call.message.answer(
            f"✅ Granted <b>{plan.title}</b> to <code>{target_id}</code> "
            f"({result.email})."
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("grant failed: %s", exc)
        await call.message.answer(f"⚠️ Grant failed: {exc}")
    await call.answer()
