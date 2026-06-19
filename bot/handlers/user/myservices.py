from __future__ import annotations

import logging

from typing import Callable
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.db import repo
from bot.db.models import OrderStatus, PaymentMethod, ServiceStatus
from bot.keyboards.user_kb import (
    migration_targets_kb,
    renew_plans_kb,
    service_actions_kb,
    services_kb,
    extra_gb_packages_kb,
    extra_time_packages_kb,
    addon_payment_methods_kb,
    upgrade_plans_kb,
    upgrade_payment_methods_kb,
)
from bot.keyboards.admin_kb import confirm_kb
from bot.payments.base import get_provider
from bot.keyboards.user_kb import delivery_kb
from bot.states.forms import CheckoutStates
from bot.db.models import Plan, Order, Service
from bot.services import provisioning
from bot.services.delivery import send_config_links_one_by_one, send_configs
from bot.services.pricing import amount_for, available_methods
from bot.services.provisioning import ProvisioningError
from bot.utils.format import fmt_expiry, fmt_quota, human_bytes, progress_bar, now_ms
from bot.utils.qr import make_qr_png

logger = logging.getLogger(__name__)
router = Router(name="user-myservices")


async def _render_service_list(target: Message, user_id: int, _: Callable[[str], str]) -> None:
    node_id = getattr(target.bot, "node_id", 0)
    services = await repo.list_user_services(user_id, node_id=node_id)
    services = [s for s in services if s.status != ServiceStatus.deleted]
    if not services:
        await target.answer(_("no_services"))
        return
    await target.answer(
        _("select_service"), reply_markup=services_kb(services)
    )


@router.message(F.text.in_(["🛡 My Services", "🛡 سرویس‌های من"]))
async def my_services(message: Message, _: Callable[[str], str]) -> None:
    await _render_service_list(message, message.from_user.id, _)


async def _service_kb(service, _: Callable[[str], str]) -> object:
    can_migrate = await provisioning.can_migrate_service(service)
    return service_actions_kb(service.id, can_migrate=can_migrate, _=_)


async def _usage_line(service) -> str:
    traffic = await provisioning.fetch_traffic(service)
    if traffic is None:
        return f"⏳ {fmt_expiry(service.expiry_time)}"
    bar = progress_bar(traffic.used, traffic.total or service.total_bytes)
    quota = fmt_quota(traffic.used, traffic.total or service.total_bytes)
    return f"{bar}\n📦 {quota}\n⏳ {fmt_expiry(traffic.expiry_time or service.expiry_time)}"


@router.callback_query(F.data.startswith("svc:usage:"))
async def svc_usage(call: CallbackQuery, _: Callable[[str], str]) -> None:
    service_id = int(call.data.rsplit(":", 1)[1])
    service = await repo.get_service(service_id)
    if service is None or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    line = await _usage_line(service)
    await call.message.answer(
        f"📊 <b>{service.email}</b>\n\n{line}",
        reply_markup=await _service_kb(service, _),
    )
    await call.answer()


@router.callback_query(F.data.startswith("svc:migrate:"))
async def svc_migrate_start(call: CallbackQuery, _: Callable[[str], str]) -> None:
    service_id = int(call.data.rsplit(":", 1)[1])
    service = await repo.get_service(service_id)
    if service is None or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    if not await provisioning.can_migrate_service(service):
        await call.answer("Migration is not available for this service.", show_alert=True)
        return
    targets = await repo.list_migration_targets(exclude_panel_id=service.panel_id)
    await call.message.answer(
        "🔀 <b>Migrate service</b>\n\n"
        f"Move <code>{service.email}</code> to another server.\n"
        "Your remaining traffic and time will be preserved.\n\n"
        "Choose the destination server:",
        reply_markup=migration_targets_kb(service.id, targets, _),
    )
    await call.answer()


@router.callback_query(F.data.startswith("svc:migrateto:"))
async def svc_migrate_confirm(call: CallbackQuery, _: Callable[[str], str]) -> None:
    _prefix, _action, service_id_raw, panel_id_raw = call.data.split(":")
    service_id = int(service_id_raw)
    panel_id = int(panel_id_raw)
    service = await repo.get_service(service_id)
    panel = await repo.get_panel(panel_id)
    if (
        service is None
        or service.user_tg_id != call.from_user.id
        or panel is None
        or not panel.allow_migrations
    ):
        await call.answer(_("service_not_found"), show_alert=True)
        return
    remaining = await provisioning.compute_remaining(service)
    data_line = (
        "∞ unlimited"
        if not remaining.total_bytes
        else human_bytes(remaining.total_bytes)
    )
    time_line = fmt_expiry(remaining.expiry_time)
    await call.message.edit_text(
        f"🔀 <b>Confirm migration</b>\n\n"
        f"From: current server\n"
        f"To: <b>{panel.name}</b>\n\n"
        f"📦 Remaining data: <b>{data_line}</b>\n"
        f"⏳ Remaining time: <b>{time_line}</b>\n\n"
        "Your old config will be removed from the current server.",
        reply_markup=confirm_kb(
            f"svc:migrateok:{service_id}:{panel_id}",
            f"svc:{service_id}",
        ),
    )
    await call.answer()


@router.callback_query(F.data.startswith("svc:migrateok:"))
async def svc_migrate_execute(call: CallbackQuery, bot: Bot, _: Callable[[str], str]) -> None:
    _prefix, _action, service_id_raw, panel_id_raw = call.data.split(":")
    service_id = int(service_id_raw)
    panel_id = int(panel_id_raw)
    service = await repo.get_service(service_id)
    panel = await repo.get_panel(panel_id)
    if service is None or service.user_tg_id != call.from_user.id or panel is None:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    await call.answer("Migrating…")
    try:
        result = await provisioning.migrate_service(service, panel_id)
    except ProvisioningError as exc:
        await call.message.edit_text(f"❌ Migration failed: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("migrate failed: %s", exc)
        await call.message.edit_text(
            "❌ Migration failed. Please try again or contact support."
        )
        return
    await call.message.edit_text(
        f"✅ Migrated to <b>{panel.name}</b>.\n"
        f"New service: <code>{result.email}</code>"
    )
    await send_configs(bot, call.from_user.id, result, title="Migration complete", _=_)


@router.callback_query(F.data.startswith("svc:configs:"))
async def svc_configs(call: CallbackQuery, bot: Bot, _: Callable[[str], str]) -> None:
    service_id = int(call.data.rsplit(":", 1)[1])
    service = await repo.get_service(service_id)
    if service is None or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    await call.answer("Fetching subscription…")
    links, sub_url = await provisioning.fetch_links(service)

    lines = [f"🔗 <b>{service.email}</b>", ""]
    if sub_url:
        urls = [u.strip() for u in sub_url.strip().split("\n") if u.strip()]
        if len(urls) > 1:
            lines.append("Subscription links:")
            lines.append("")
            for url in urls:
                from urllib.parse import urlparse
                domain = urlparse(url).hostname or url
                lines.append(f"🌐 <b>{domain}</b>:")
                lines.append(f"<code>{url}</code>")
                lines.append("")
        else:
            lines.append("Subscription link:")
            lines.append(f"<code>{urls[0]}</code>")
            lines.append("")
        lines.append("📷 Scan the QR to import into your VPN app.")
        qr_target = urls[0] if urls else None
    else:
        lines.append("⚠️ No subscription link found. Contact support.")
        qr_target = None
    text = "\n".join(lines)
    markup = delivery_kb(service.id, _)

    if qr_target:
        try:
            png = make_qr_png(qr_target)
            if len(text) <= 1024:
                await bot.send_photo(
                    call.from_user.id,
                    BufferedInputFile(png.read(), filename="sub.png"),
                    caption=text,
                    reply_markup=markup,
                )
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning("QR send failed: %s", exc)

    await call.message.answer(
        text, disable_web_page_preview=True, reply_markup=markup
    )


@router.callback_query(F.data.startswith("svc:sendlinks:"))
async def svc_send_links(call: CallbackQuery, bot: Bot, _: Callable[[str], str]) -> None:
    service_id = int(call.data.rsplit(":", 1)[1])
    service = await repo.get_service(service_id)
    if service is None or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    await call.answer("Sending config links…")
    links, _sub_url = await provisioning.fetch_links(service)
    if not links:
        await call.message.answer("⚠️ No config links found for this service.")
        return
    count = await send_config_links_one_by_one(bot, call.from_user.id, links)
    if count > 1:
        await call.message.answer(f"✅ Sent {count} config links.")


@router.callback_query(F.data.startswith("svc:regen:"))
async def svc_regenerate(call: CallbackQuery, bot: Bot, _: Callable[[str], str]) -> None:
    service_id = int(call.data.rsplit(":", 1)[1])
    service = await repo.get_service(service_id)
    if service is None or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    await call.answer("Regenerating config…")
    try:
        result = await provisioning.regenerate_client(service)
        await call.message.answer("✅ New config generated. Check your new links:")
        await send_configs(bot, call.from_user.id, result, title="New Config", _=_)
    except Exception as exc:  # noqa: BLE001
        logger.warning("regenerate failed: %s", exc)
        await call.answer("Failed to regenerate. Try later.", show_alert=True)


@router.callback_query(F.data.startswith("svc:renew:"))
async def svc_renew(call: CallbackQuery, _: Callable[[str], str]) -> None:
    service_id = int(call.data.rsplit(":", 1)[1])
    service = await repo.get_service(service_id)
    if service is None or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    node_id = getattr(call.bot, "node_id", 0)
    plans = await repo.list_plans(only_active=True, node_id=node_id)
    if not plans:
        await call.answer(_("renew_not_available"), show_alert=True)
        return
    await call.message.answer(
        _("renew_which_plan"),
        reply_markup=renew_plans_kb(service.id, plans, _),
    )
    await call.answer()


@router.callback_query(F.data.startswith("renewplan:"))
async def renew_plan_pick(call: CallbackQuery) -> None:
    _prefix, service_id_raw, plan_id_raw = call.data.split(":")
    service = await repo.get_service(int(service_id_raw))
    plan = await repo.get_plan(int(plan_id_raw))
    if service is None or service.user_tg_id != call.from_user.id or plan is None:
        await call.answer("Not found.", show_alert=True)
        return
    methods = available_methods(plan)
    if not methods:
        await call.answer("No payment methods for this plan.", show_alert=True)
        return
    # Reuse the buy flow but tag the order with renew_service_id via a dedicated cb.
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    labels = {
        PaymentMethod.card: "💳 Card-to-card",
        PaymentMethod.stars: "⭐ Telegram Stars",
        PaymentMethod.crypto: "🪙 Crypto",
    }
    for method in methods:
        builder.button(
            text=labels.get(method, method.value),
            callback_data=f"rbuy:{service.id}:{plan.id}:{method.value}",
        )
    builder.adjust(1)
    await call.message.edit_text(
        f"🔄 Renew <code>{service.email}</code> with <b>{plan.title}</b>.\n"
        "Choose a payment method:",
        reply_markup=builder.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("rbuy:"))
async def renew_buy(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    _prefix, service_id_raw, plan_id_raw, method_raw = call.data.split(":")
    service = await repo.get_service(int(service_id_raw))
    plan = await repo.get_plan(int(plan_id_raw))
    if service is None or service.user_tg_id != call.from_user.id or plan is None:
        await call.answer("Not found.", show_alert=True)
        return
    try:
        method = PaymentMethod(method_raw)
    except ValueError:
        await call.answer("Unknown method.", show_alert=True)
        return
    amount, currency = amount_for(plan, method)
    node_id = getattr(bot, "node_id", 0)
    order = await repo.create_order(
        user_tg_id=call.from_user.id,
        plan_id=plan.id,
        method=method,
        amount=amount,
        currency=currency,
        renew_service_id=service.id,
        status=OrderStatus.pending,
        node_id=node_id,
    )
    provider = get_provider(method)
    await provider.start_checkout(bot, call.from_user.id, order, plan, state)
    await call.answer()


@router.callback_query(F.data.regexp(r"^svc:\d+$"))
async def svc_open(call: CallbackQuery, _: Callable[[str], str]) -> None:
    service_id = int(call.data.split(":", 1)[1])
    service = await repo.get_service(service_id)
    if service is None or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    line = await _usage_line(service)
    status_emoji = "🟢" if service.status == ServiceStatus.active else "🔴"
    await call.message.answer(
        f"{status_emoji} <b>{service.email}</b>\n\n{line}",
        reply_markup=await _service_kb(service, _),
    )
    await call.answer()


@router.callback_query(F.data.startswith("svc:delete:"))
async def svc_delete_confirm(call: CallbackQuery, _: Callable[[str], str]) -> None:
    service_id = int(call.data.rsplit(":", 1)[1])
    service = await repo.get_service(service_id)
    if service is None or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    await call.message.edit_text(
        _("delete_service_confirm"),
        reply_markup=confirm_kb(
            f"svc:deleteok:{service.id}",
            f"svc:{service.id}"
        ),
    )
    await call.answer()


@router.callback_query(F.data.startswith("svc:deleteok:"))
async def svc_delete_execute(call: CallbackQuery, _: Callable[[str], str]) -> None:
    service_id = int(call.data.rsplit(":", 1)[1])
    service = await repo.get_service(service_id)
    if service is None or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    
    await call.answer("Deleting…")
    try:
        await provisioning.delete_service(service)
    except Exception as exc:
        await call.message.edit_text(f"❌ Deletion failed: {exc}")
        return
    
    await call.message.edit_text(_("service_deleted"))


async def get_upgrade_details(service: Service, new_plan: Plan, method: PaymentMethod) -> dict:
    new_price, currency = amount_for(new_plan, method)
    old_plan = await repo.get_plan(service.plan_id) if service.plan_id else None
    
    old_price = 0.0
    remaining_gb = 0.0
    remaining_days = 0.0
    remaining_val = 0.0
    
    if old_plan:
        old_price, _ = amount_for(old_plan, method)
        remaining = await provisioning.compute_remaining(service)
        
        traffic_ratio = 1.0
        if old_plan.traffic_gb > 0:
            remaining_gb = remaining.total_bytes / (1024**3)
            traffic_ratio = remaining_gb / old_plan.traffic_gb
            traffic_ratio = max(0.0, min(1.0, traffic_ratio))
            
        time_ratio = 1.0
        if old_plan.duration_days > 0:
            remaining_time_ms = remaining.expiry_time - now_ms()
            remaining_days = max(0.0, remaining_time_ms / (24 * 3600 * 1000))
            time_ratio = remaining_time_ms / (old_plan.duration_days * 86400 * 1000)
            time_ratio = max(0.0, min(1.0, time_ratio))
            
        remaining_ratio = min(traffic_ratio, time_ratio)
        remaining_val = old_price * remaining_ratio
        remaining_val = max(0.0, min(old_price, remaining_val))
        
    amount_to_pay = max(0.0, new_price - remaining_val)
    
    return {
        "new_price": new_price,
        "currency": currency,
        "old_price": old_price,
        "remaining_gb": remaining_gb,
        "remaining_days": remaining_days,
        "remaining_val": remaining_val,
        "amount_to_pay": amount_to_pay,
    }


@router.callback_query(F.data.startswith("svc:upgrade:"))
async def svc_upgrade(call: CallbackQuery, _: Callable[[str], str]) -> None:
    service_id = int(call.data.rsplit(":", 1)[1])
    service = await repo.get_service(service_id)
    if service is None or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    node_id = getattr(call.bot, "node_id", 0)
    plans = await repo.list_plans(only_active=True, node_id=node_id)
    
    old_plan = await repo.get_plan(service.plan_id) if service.plan_id else None
    if old_plan:
        plans = [p for p in plans if not p.is_trial and p.id != service.plan_id and p.price_fiat > old_plan.price_fiat]
    else:
        plans = [p for p in plans if not p.is_trial]

    if not plans:
        await call.answer(_("upgrade_not_available"), show_alert=True)
        return

    await call.message.answer(
        _("upgrade_which_plan"),
        reply_markup=upgrade_plans_kb(service.id, plans, _),
    )
    await call.answer()


@router.callback_query(F.data.startswith("upgplan:"))
async def upgrade_plan_pick(call: CallbackQuery, _: Callable[[str], str]) -> None:
    _prefix, service_id_raw, plan_id_raw = call.data.split(":")
    service = await repo.get_service(int(service_id_raw))
    plan = await repo.get_plan(int(plan_id_raw))
    if service is None or service.user_tg_id != call.from_user.id or plan is None:
        await call.answer("Not found.", show_alert=True)
        return

    details = await get_upgrade_details(service, plan, PaymentMethod.card)
    amount_to_pay = details["amount_to_pay"]
    currency = details["currency"]

    old_plan = await repo.get_plan(service.plan_id) if service.plan_id else None
    old_title = old_plan.title if old_plan else "Custom"
    
    text = _(
        "upgrade_confirm",
        email=service.email,
        title=plan.title,
        old_title=old_title,
        old_price=int(details["old_price"]),
        currency=currency,
        remaining_gb=details["remaining_gb"],
        remaining_days=details["remaining_days"],
        remaining_val=int(details["remaining_val"]),
        new_price=int(details["new_price"]),
        amount_to_pay=int(amount_to_pay),
    )

    methods = available_methods(plan)
    node_id = getattr(call.bot, "node_id", 0)
    balance = await repo.get_balance(call.from_user.id, node_id=node_id)
    
    can_pay_with_balance = balance >= amount_to_pay and amount_to_pay > 0

    await call.message.edit_text(
        text,
        reply_markup=upgrade_payment_methods_kb(
            service.id,
            plan.id,
            methods,
            can_pay_with_balance,
            _
        )
    )
    await call.answer()


async def _pay_upgrade_with_balance(
    call: CallbackQuery,
    bot: Bot,
    service: Service,
    plan: Plan,
    amount: float,
    currency: str,
    _: Callable[[str], str]
) -> None:
    from bot.db.repo import InsufficientBalance
    node_id = getattr(bot, "node_id", 0)
    balance = await repo.get_balance(call.from_user.id, node_id=node_id)
    if balance < amount:
        await call.answer(
            _("insufficient_balance", current=int(balance), price=int(amount), currency=currency),
            show_alert=True,
        )
        return

    try:
        await repo.adjust_balance(
            call.from_user.id,
            -amount,
            reason=f"Upgrade: svc #{service.id} to plan #{plan.id}",
            node_id=node_id
        )
    except InsufficientBalance:
        await call.answer(_("insufficient_balance_short"), show_alert=True)
        return

    order = await repo.create_order(
        user_tg_id=call.from_user.id,
        plan_id=plan.id,
        method=PaymentMethod.wallet,
        amount=amount,
        currency=currency,
        renew_service_id=service.id,
        status=OrderStatus.pending,
        kind="upgrade",
        node_id=node_id,
    )
    
    from bot.services.fulfillment import fulfill_order
    await call.message.answer("⏳ Paid from balance. Upgrading service...")
    ok = await fulfill_order(bot, order)
    if not ok:
        # refund on provisioning failure
        await repo.adjust_balance(
            call.from_user.id,
            amount,
            reason=f"Refund: Upgrade failed for svc #{service.id} (order #{order.id})",
            node_id=node_id
        )
        await call.message.answer("⚠️ Upgrade failed; your balance was refunded.")
    await call.answer()


@router.callback_query(F.data.startswith("upgbuy:"))
async def upgrade_buy(call: CallbackQuery, bot: Bot, state: FSMContext, _: Callable[[str], str]) -> None:
    _prefix, service_id_raw, plan_id_raw, method_raw = call.data.split(":")
    service = await repo.get_service(int(service_id_raw))
    plan = await repo.get_plan(int(plan_id_raw))
    if service is None or service.user_tg_id != call.from_user.id or plan is None:
        await call.answer("Not found.", show_alert=True)
        return
    try:
        method = PaymentMethod(method_raw)
    except ValueError:
        await call.answer("Unknown method.", show_alert=True)
        return

    details = await get_upgrade_details(service, plan, method)
    amount = details["amount_to_pay"]
    currency = details["currency"]

    if method == PaymentMethod.wallet:
        await _pay_upgrade_with_balance(call, bot, service, plan, amount, currency, _)
        return

    if amount <= 0:
        await call.answer("This upgrade is free or already paid.", show_alert=True)
        return

    node_id = getattr(bot, "node_id", 0)
    order = await repo.create_order(
        user_tg_id=call.from_user.id,
        plan_id=plan.id,
        method=method,
        amount=amount,
        currency=currency,
        renew_service_id=service.id,
        status=OrderStatus.pending,
        kind="upgrade",
        node_id=node_id,
    )

    plan_mock = Plan(
        id=plan.id,
        title=f"Upgrade: {plan.title}",
        description=f"Upgrade service {service.email} to {plan.title}",
        price_fiat=amount if method == PaymentMethod.card else 0.0,
        price_stars=int(amount) if method == PaymentMethod.stars else 0,
        price_usd=amount if method == PaymentMethod.crypto else 0.0,
    )

    provider = get_provider(method)
    await call.message.answer(_("preparing_order"))
    await provider.start_checkout(bot, call.from_user.id, order, plan_mock, state)
    await call.answer()


# --- Buying Extra GB & Time ---

def calculate_addon_price_and_currency(plan: Optional[Plan], kind: str, qty: float, method: PaymentMethod) -> tuple[float, str]:
    from bot.config import get_settings
    settings = get_settings()
    
    # 1. Strict mode package pricing lookup
    if plan:
        mode = plan.extra_gb_mode if kind == "extra_gb" else plan.extra_time_mode
        packages = plan.extra_gb_packages if kind == "extra_gb" else plan.extra_time_packages
        if mode == "strict" and packages:
            for pkg in packages:
                pkg_val = float(pkg.get("gb" if kind == "extra_gb" else "days", 0))
                if abs(pkg_val - qty) < 0.01:
                    if method == PaymentMethod.card or method == PaymentMethod.wallet:
                        return float(pkg.get("price_fiat") or 0.0), settings.fiat_currency
                    if method == PaymentMethod.stars:
                        return float(int(pkg.get("price_stars") or 0)), "XTR"
                    if method == PaymentMethod.crypto:
                        return float(pkg.get("price_usd") or 0.0), "USD"
            return 0.0, settings.fiat_currency
            
    # 2. Flexible mode pricing lookup
    unit_fiat, unit_stars, unit_usd = 0.0, 0, 0.0
    if plan:
        if kind == "extra_gb":
            unit_fiat = plan.extra_gb_price_fiat if plan.extra_gb_price_fiat is not None else settings.extra_gb_price_fiat
            unit_stars = plan.extra_gb_price_stars if plan.extra_gb_price_stars is not None else settings.extra_gb_price_stars
            unit_usd = plan.extra_gb_price_usd if plan.extra_gb_price_usd is not None else settings.extra_gb_price_usd
        elif kind == "extra_time":
            unit_fiat = plan.extra_time_price_fiat if plan.extra_time_price_fiat is not None else settings.extra_time_price_fiat
            unit_stars = plan.extra_time_price_stars if plan.extra_time_price_stars is not None else settings.extra_time_price_stars
            unit_usd = plan.extra_time_price_usd if plan.extra_time_price_usd is not None else settings.extra_time_price_usd
    else:
        if kind == "extra_gb":
            unit_fiat = settings.extra_gb_price_fiat
            unit_stars = settings.extra_gb_price_stars
            unit_usd = settings.extra_gb_price_usd
        elif kind == "extra_time":
            unit_fiat = settings.extra_time_price_fiat
            unit_stars = settings.extra_time_price_stars
            unit_usd = settings.extra_time_price_usd

    if method == PaymentMethod.card or method == PaymentMethod.wallet:
        return float(qty * unit_fiat), settings.fiat_currency
    if method == PaymentMethod.stars:
        return float(int(qty * unit_stars)), "XTR"
    if method == PaymentMethod.crypto:
        return float(qty * unit_usd), "USD"
    return 0.0, settings.fiat_currency


async def _show_addon_checkout(target_message: Message, service: Service, kind: str, qty: float, _: Callable[[str], str]) -> None:
    from bot.config import get_settings
    settings = get_settings()
    plan = await repo.get_plan(service.plan_id) if service.plan_id else None
    
    # Calculate price per currency
    price_fiat, fiat_currency = calculate_addon_price_and_currency(plan, kind, qty, PaymentMethod.card)
    price_stars, stars_currency = calculate_addon_price_and_currency(plan, kind, qty, PaymentMethod.stars)
    price_usd, usd_currency = calculate_addon_price_and_currency(plan, kind, qty, PaymentMethod.crypto)
    
    methods = []
    if price_fiat > 0 and settings.card_number:
        methods.append(PaymentMethod.card)
    if price_stars > 0 and settings.stars_enabled:
        methods.append(PaymentMethod.stars)
    if price_usd > 0 and settings.crypto_enabled:
        methods.append(PaymentMethod.crypto)
        
    node_id = getattr(target_message.bot, "node_id", 0)
    balance = await repo.get_balance(target_message.from_user.id, node_id=node_id)
    can_pay_with_balance = price_fiat > 0 and balance >= price_fiat
    
    unit = "GB" if kind == "extra_gb" else _("days_label")
    caption = _("addon_checkout_title", email=service.email, qty=qty, unit=unit)
    
    # Prices details
    price_parts = []
    if PaymentMethod.card in methods:
        price_parts.append(f"💳 Card: {int(price_fiat):,} {settings.fiat_currency}")
    if PaymentMethod.stars in methods:
        price_parts.append(f"⭐ Stars: {int(price_stars)}")
    if PaymentMethod.crypto in methods:
        price_parts.append(f"🪙 Crypto: ${price_usd:g}")
        
    if price_parts:
        caption += "\n\n💵 " + "\n💵 ".join(price_parts)
        
    if can_pay_with_balance:
        caption += f"\n\n💰 Your balance: {int(balance):,} {settings.fiat_currency}"
        
    await target_message.answer(
        caption,
        reply_markup=addon_payment_methods_kb(
            service.id,
            kind,
            qty,
            methods,
            can_pay_with_balance,
            _
        )
    )


async def _pay_addon_with_balance(call: CallbackQuery, bot: Bot, service_id: int, kind: str, qty: float, _: Callable[[str], str]) -> None:
    from bot.db.repo import InsufficientBalance
    from bot.config import get_settings
    settings = get_settings()
    service = await repo.get_service(service_id)
    plan = await repo.get_plan(service.plan_id) if (service and service.plan_id) else None
    
    price, currency = calculate_addon_price_and_currency(plan, kind, qty, PaymentMethod.wallet)
    if price <= 0:
        await call.answer("This addon cannot be paid from balance.", show_alert=True)
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
            call.from_user.id, -price, reason=f"Addon: {qty} {kind} for svc #{service_id}", node_id=node_id
        )
    except InsufficientBalance:
        await call.answer(_("insufficient_balance_short"), show_alert=True)
        return

    order = await repo.create_order(
        user_tg_id=call.from_user.id,
        plan_id=service.plan_id if (service and service.plan_id) else 0,
        method=PaymentMethod.wallet,
        amount=price,
        currency=currency,
        renew_service_id=service_id,
        status=OrderStatus.pending,
        kind=kind,
        node_id=node_id,
        extra_gb=qty if kind == "extra_gb" else None,
        extra_days=int(qty) if kind == "extra_time" else None,
    )
    
    from bot.services.fulfillment import fulfill_order
    await call.message.answer("⏳ Paid from balance. Applying package...")
    ok = await fulfill_order(bot, order)
    if not ok:
        # refund on provisioning failure
        await repo.adjust_balance(
            call.from_user.id, price, reason=f"Refund: Addon svc #{service_id} (order #{order.id})", node_id=node_id
        )
        await call.message.answer("⚠️ Processing failed; your balance was refunded.")
    await call.answer()


@router.callback_query(F.data.startswith("svc:buy_gb:"))
async def svc_buy_gb_menu(call: CallbackQuery, _: Callable[[str], str]) -> None:
    service_id = int(call.data.rsplit(":", 1)[1])
    service = await repo.get_service(service_id)
    if not service or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    
    plan = await repo.get_plan(service.plan_id) if service.plan_id else None
    caption = _("extra_gb_title", email=service.email)
    await call.message.edit_text(caption, reply_markup=extra_gb_packages_kb(service_id, plan, _))
    await call.answer()


@router.callback_query(F.data.startswith("svc:buy_time:"))
async def svc_buy_time_menu(call: CallbackQuery, _: Callable[[str], str]) -> None:
    service_id = int(call.data.rsplit(":", 1)[1])
    service = await repo.get_service(service_id)
    if not service or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    
    plan = await repo.get_plan(service.plan_id) if service.plan_id else None
    caption = _("extra_time_title", email=service.email)
    await call.message.edit_text(caption, reply_markup=extra_time_packages_kb(service_id, plan, _))
    await call.answer()


@router.callback_query(F.data.startswith("svc:buy_gb_pkg:"))
async def svc_buy_gb_pkg(call: CallbackQuery, _: Callable[[str], str]) -> None:
    parts = call.data.split(":")
    service_id = int(parts[2])
    gb = float(parts[3])
    
    service = await repo.get_service(service_id)
    if not service or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
        
    await call.answer()
    await _show_addon_checkout(call.message, service, "extra_gb", gb, _)


@router.callback_query(F.data.startswith("svc:buy_time_pkg:"))
async def svc_buy_time_pkg(call: CallbackQuery, _: Callable[[str], str]) -> None:
    parts = call.data.split(":")
    service_id = int(parts[2])
    days = float(parts[3])
    
    service = await repo.get_service(service_id)
    if not service or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
        
    await call.answer()
    await _show_addon_checkout(call.message, service, "extra_time", days, _)


@router.callback_query(F.data.startswith("svc:buy_custom_gb:"))
async def svc_buy_custom_gb(call: CallbackQuery, state: FSMContext, _: Callable[[str], str]) -> None:
    service_id = int(call.data.rsplit(":", 1)[1])
    service = await repo.get_service(service_id)
    if not service or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    
    plan = await repo.get_plan(service.plan_id) if service.plan_id else None
    if plan and plan.extra_gb_mode == "strict":
        await call.answer("This plan only supports predefined packages.", show_alert=True)
        return
        
    await state.set_state(CheckoutStates.entering_extra_gb)
    await state.update_data(service_id=service_id)
    await call.message.answer(_("enter_custom_gb"))
    await call.answer()


@router.callback_query(F.data.startswith("svc:buy_custom_time:"))
async def svc_buy_custom_time(call: CallbackQuery, state: FSMContext, _: Callable[[str], str]) -> None:
    service_id = int(call.data.rsplit(":", 1)[1])
    service = await repo.get_service(service_id)
    if not service or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
    
    plan = await repo.get_plan(service.plan_id) if service.plan_id else None
    if plan and plan.extra_time_mode == "strict":
        await call.answer("This plan only supports predefined packages.", show_alert=True)
        return
        
    await state.set_state(CheckoutStates.entering_extra_days)
    await state.update_data(service_id=service_id)
    await call.message.answer(_("enter_custom_time"))
    await call.answer()


@router.message(CheckoutStates.entering_extra_gb)
async def process_custom_gb_input(message: Message, state: FSMContext, _: Callable[[str], str]) -> None:
    text = message.text.strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Cancelled.")
        return
    try:
        qty = float(text)
        if qty <= 0:
            raise ValueError
    except ValueError:
        await message.answer(_("invalid_amount"))
        return

    data = await state.get_data()
    service_id = data.get("service_id")
    
    service = await repo.get_service(service_id)
    if not service:
        await state.clear()
        await message.answer(_("service_not_found"))
        return

    plan = await repo.get_plan(service.plan_id) if service.plan_id else None
    if plan and plan.extra_gb_mode == "strict":
        await state.clear()
        await message.answer("❌ This plan only supports predefined extra GB packages.")
        return

    await state.clear()
    await _show_addon_checkout(message, service, "extra_gb", qty, _)


@router.message(CheckoutStates.entering_extra_days)
async def process_custom_days_input(message: Message, state: FSMContext, _: Callable[[str], str]) -> None:
    text = message.text.strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Cancelled.")
        return
    try:
        qty = int(text)
        if qty <= 0:
            raise ValueError
    except ValueError:
        await message.answer(_("invalid_amount"))
        return

    data = await state.get_data()
    service_id = data.get("service_id")
    
    service = await repo.get_service(service_id)
    if not service:
        await state.clear()
        await message.answer(_("service_not_found"))
        return

    plan = await repo.get_plan(service.plan_id) if service.plan_id else None
    if plan and plan.extra_time_mode == "strict":
        await state.clear()
        await message.answer("❌ This plan only supports predefined extra Time packages.")
        return

    await state.clear()
    await _show_addon_checkout(message, service, "extra_time", float(qty), _)


@router.callback_query(F.data.startswith("abuy:"))
async def addon_buy_handler(call: CallbackQuery, bot: Bot, state: FSMContext, _: Callable[[str], str]) -> None:
    parts = call.data.split(":")
    service_id = int(parts[1])
    kind = parts[2]
    qty = float(parts[3])
    method_raw = parts[4]
    
    service = await repo.get_service(service_id)
    if service is None or service.user_tg_id != call.from_user.id:
        await call.answer(_("service_not_found"), show_alert=True)
        return
        
    try:
        method = PaymentMethod(method_raw)
    except ValueError:
        await call.answer(_("unknown_method"), show_alert=True)
        return
        
    if method == PaymentMethod.wallet:
        await _pay_addon_with_balance(call, bot, service_id, kind, qty, _)
        return
    
    plan = await repo.get_plan(service.plan_id) if service.plan_id else None
    amount, currency = calculate_addon_price_and_currency(plan, kind, qty, method)
    if amount <= 0:
        await call.answer(_("method_unavailable"), show_alert=True)
        return
        
    node_id = getattr(bot, "node_id", 0)
    
    order = await repo.create_order(
        user_tg_id=call.from_user.id,
        plan_id=service.plan_id if (service and service.plan_id) else 0,
        method=method,
        amount=amount,
        currency=currency,
        renew_service_id=service_id,
        status=OrderStatus.pending,
        kind=kind,
        node_id=node_id,
        extra_gb=qty if kind == "extra_gb" else None,
        extra_days=int(qty) if kind == "extra_time" else None,
    )
    
    plan_mock = Plan(
        id=0,
        title=f"Addon: {qty} {'GB' if kind == 'extra_gb' else 'Days'}",
        description=f"Buying extra {qty} {'GB' if kind == 'extra_gb' else 'Days'} of validity for config {service.email}",
        price_fiat=amount if method == PaymentMethod.card else 0.0,
        price_stars=int(amount) if method == PaymentMethod.stars else 0,
        price_usd=amount if method == PaymentMethod.crypto else 0.0,
    )
    
    provider = get_provider(method)
    await call.message.answer(_("preparing_order"))
    await provider.start_checkout(bot, call.from_user.id, order, plan_mock, state)
    await call.answer()
