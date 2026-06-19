from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from bot.config import get_settings
from bot.db import repo
from bot.db.models import Order, OrderKind, OrderStatus
from bot.services import provisioning
from bot.services.delivery import send_configs
from bot.utils.locales import get_text
from functools import partial

logger = logging.getLogger(__name__)

_fulfillment_locks: dict[int, asyncio.Lock] = {}
_locks_lock: asyncio.Lock | None = None


async def _get_order_lock(order_id: int) -> asyncio.Lock:
    global _locks_lock
    if _locks_lock is None:
        _locks_lock = asyncio.Lock()
    async with _locks_lock:
        if order_id not in _fulfillment_locks:
            _fulfillment_locks[order_id] = asyncio.Lock()
        return _fulfillment_locks[order_id]


async def fulfill_order(bot: Bot, order: Order) -> bool:
    """Provision (or renew) the service for a paid order and deliver configs.

    Idempotent: if the order is already paid, it does nothing.
    """
    lock = await _get_order_lock(order.id)
    async with lock:
        # Reload order from database to get the absolute latest status
        db_order = await repo.get_order(order.id)
        if db_order is None:
            logger.error("Order %s not found in database", order.id)
            return False
        order = db_order

        if order.status == OrderStatus.paid:
            logger.info("Order %s already fulfilled", order.id)
            return True

        # Wallet top-up: credit the balance instead of provisioning.
        if order.kind == OrderKind.topup.value:
            return await _fulfill_topup(bot, order)

        # Extra GB/Time addon purchases
        if order.kind in (OrderKind.extra_gb.value, OrderKind.extra_time.value):
            return await _fulfill_addon(bot, order)

        if order.kind == OrderKind.upgrade.value:
            return await _fulfill_upgrade(bot, order)

        plan = await repo.get_plan(order.plan_id)
        if plan is None:
            logger.error("Order %s references missing plan %s", order.id, order.plan_id)
            await repo.set_order_status(order.id, OrderStatus.rejected)
            return False

        # ----------------------------- Reseller Balance Check -----------------------------
        node_id = getattr(bot, "node_id", 0)
        is_reseller_order = (node_id > 0)
        deduction = 0.0
        reseller_tg_id = None
        qty = getattr(order, "quantity", 1) or 1

        if is_reseller_order:
            node = await repo.get_node(node_id)
            if not node:
                logger.error("Fulfill failed: Node bot #%d not found in DB.", node_id)
                return False

            reseller_tg_id = node.owner_tg_id
            reseller = await repo.get_user(reseller_tg_id, 0)
            if not reseller:
                logger.error("Fulfill failed: Reseller owner %d not found in DB.", reseller_tg_id)
                return False

            if plan.traffic_gb == 0:
                deduction = await repo.get_reseller_unlimited_price(reseller_tg_id, plan.panel_id)
            else:
                gb_price = await repo.get_reseller_gb_price(reseller_tg_id, plan.panel_id)
                deduction = float(plan.traffic_gb * gb_price)
            deduction = deduction * qty
            if reseller.balance < deduction:
                logger.warning("Fulfill delayed: Reseller %d has insufficient balance (%s < %s)", reseller_tg_id, reseller.balance, deduction)
                
                # Notify reseller
                try:
                    await bot.send_message(
                        reseller_tg_id,
                        f"⚠️ <b>Reseller Balance Alert</b>\n\n"
                        f"Your customer (ID: <code>{order.user_tg_id}</code>) tried to purchase/renew plan <b>{plan.title}</b> (Qty: {qty}).\n"
                        f"Required cost: <b>{int(deduction):,} tomans</b>.\n"
                        f"Your balance: <b>{int(reseller.balance):,} tomans</b>.\n\n"
                        f"❌ Order #{order.id} fulfillment failed due to insufficient reseller balance. Please top up your balance in the Main Bot.",
                    )
                except Exception:
                    pass
                
                # Notify buyer
                try:
                    await bot.send_message(
                        order.user_tg_id,
                        "⚠️ <b>Fulfillment delayed</b>\n\n"
                        "Your payment was received but we are experiencing a minor delay in provisioning. Our support team has been notified and will resolve this shortly.",
                    )
                except Exception:
                    pass
                return False

            # Deduct balance before provisioning
            await repo.adjust_balance(
                tg_id=reseller_tg_id,
                amount=-deduction,
                reason=f"Reseller customer purchase: {qty}x {plan.title} (Order #{order.id})",
                node_id=0
            )

        # ----------------------------- Provisioning -----------------------------
        results = []
        try:
            if order.renew_service_id:
                service = await repo.get_service(order.renew_service_id)
                if service is None:
                    raise RuntimeError("service to renew not found")
                result = await provisioning.renew_service(service, plan)
                results.append(result)
                title = "Service renewed"
            else:
                for _ in range(qty):
                    result = await provisioning.provision_for_plan(
                        order.user_tg_id, plan, order_id=order.id
                    )
                    results.append(result)
                title = "Your VPN is ready"
        except Exception as exc:  # noqa: BLE001
            logger.exception("Provisioning failed for order %s: %s", order.id, exc)
            
            # Refund reseller if this was a reseller bot order
            if is_reseller_order and reseller_tg_id is not None:
                try:
                    await repo.adjust_balance(
                        tg_id=reseller_tg_id,
                        amount=deduction,
                        reason=f"Refund: Provisioning failed for Order #{order.id}",
                        node_id=0
                    )
                except Exception as refund_exc:
                    logger.error("Reseller refund failed for reseller %d: %s", reseller_tg_id, refund_exc)

            try:
                await bot.send_message(
                    order.user_tg_id,
                    "⚠️ Payment received but provisioning failed. Our team has been "
                    "notified and will sort it out shortly.",
                )
            except Exception:  # noqa: BLE001
                pass
            await _notify_admins(
                bot, f"❗️ Provisioning FAILED for order #{order.id}: {exc}"
            )
            return False

        await repo.set_order_status(order.id, OrderStatus.paid)
        user = await repo.get_user(order.user_tg_id)
        lang = user.lang if user and user.lang else "en"
        _ = partial(get_text, lang)
        
        for idx, result in enumerate(results, start=1):
            custom_title = f"{title} ({idx}/{qty})" if qty > 1 else title
            await send_configs(bot, order.user_tg_id, result, title=custom_title, _=_)
        # Notify other admins of the sale, but not the buyer themselves (avoids a
        # duplicate message when an admin buys/tests their own order).
        emails_str = ", ".join([r.email for r in results])
        await _notify_admins(
            bot,
            f"✅ Order #{order.id} fulfilled for user {order.user_tg_id} "
            f"({emails_str}).",
            exclude={order.user_tg_id},
        )
        return True


async def _fulfill_topup(bot: Bot, order: Order) -> bool:
    new_balance = await repo.adjust_balance(
        order.user_tg_id,
        amount=order.amount,
        reason=f"Wallet top-up (order #{order.id})",
        order_id=order.id,
    )
    await repo.set_order_status(order.id, OrderStatus.paid)
    try:
        await bot.send_message(
            order.user_tg_id,
            f"💰 Your wallet was charged with <b>{int(order.amount):,} "
            f"{order.currency}</b>.\nNew balance: <b>{int(new_balance):,} "
            f"{order.currency}</b>.",
        )
    except Exception:  # noqa: BLE001
        pass
    await _notify_admins(
        bot,
        f"💰 Top-up #{order.id}: user {order.user_tg_id} +{int(order.amount):,} "
        f"{order.currency} (balance {int(new_balance):,}).",
        exclude={order.user_tg_id},
    )
    return True


async def _notify_admins(bot: Bot, text: str, exclude: set[int] | None = None) -> None:
    skip = exclude or set()
    node_id = getattr(bot, "node_id", 0)
    if node_id == 0:
        admins = get_settings().admin_ids
    else:
        node = await repo.get_node(node_id)
        admins = [node.owner_tg_id] if node else []
    for admin_id in admins:
        if admin_id in skip:
            continue
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001
            continue


async def _fulfill_addon(bot: Bot, order: Order) -> bool:
    node_id = getattr(bot, "node_id", 0)
    is_reseller_order = (node_id > 0)
    deduction = 0.0
    reseller_tg_id = None

    if is_reseller_order and order.kind in (OrderKind.extra_gb.value, OrderKind.extra_time.value):
        node = await repo.get_node(node_id)
        if not node:
            logger.error("Fulfill failed: Node bot #%d not found in DB.", node_id)
            return False

        reseller_tg_id = node.owner_tg_id
        reseller = await repo.get_user(reseller_tg_id, 0)
        if not reseller:
            logger.error("Fulfill failed: Reseller owner %d not found in DB.", reseller_tg_id)
            return False

        if order.kind == OrderKind.extra_gb.value:
            qty = order.extra_gb or 0.0
            unit_str = f"{qty} GB"
            panel_id = None
            if order.renew_service_id:
                service = await repo.get_service(order.renew_service_id)
                if service:
                    panel_id = service.panel_id
            gb_price = await repo.get_reseller_gb_price(reseller_tg_id, panel_id)
            deduction = float(qty * gb_price)
        else:
            qty = order.extra_days or 0
            unit_str = f"{qty} Days"
            deduction = float(qty * getattr(reseller, "reseller_day_price", 0.0))

        if reseller.balance < deduction:
            logger.warning("Fulfill delayed: Reseller %d has insufficient balance (%s < %s)", reseller_tg_id, reseller.balance, deduction)
            # Notify reseller
            try:
                await bot.send_message(
                    reseller_tg_id,
                    f"⚠️ <b>Reseller Balance Alert</b>\n\n"
                    f"Your customer (ID: <code>{order.user_tg_id}</code>) tried to purchase extra addon ({unit_str}).\n"
                    f"Required cost: <b>{int(deduction):,} tomans</b>.\n"
                    f"Your balance: <b>{int(reseller.balance):,} tomans</b>.\n\n"
                    f"❌ Order #{order.id} fulfillment failed due to insufficient reseller balance.",
                )
            except Exception:
                pass
            
            # Notify buyer
            try:
                await bot.send_message(
                    order.user_tg_id,
                    "⚠️ <b>Fulfillment delayed</b>\n\n"
                    "Your payment was received but we are experiencing a minor delay in provisioning. Our support team has been notified and will resolve this shortly.",
                )
            except Exception:
                pass
            return False

        # Deduct reseller balance before provisioning
        addon_name = "GB" if order.kind == OrderKind.extra_gb.value else "Time"
        await repo.adjust_balance(
            tg_id=reseller_tg_id,
            amount=-deduction,
            reason=f"Reseller customer extra {addon_name}: {unit_str} (Order #{order.id})",
            node_id=0
        )

    # Apply addon to service
    try:
        service = await repo.get_service(order.renew_service_id)
        if service is None:
            raise RuntimeError("service to extend not found")
            
        add_gb = order.extra_gb or 0.0
        add_days = order.extra_days or 0
        
        result = await provisioning.extend_service(service, add_days=add_days, add_gb=add_gb)
        title = "Package applied"
    except Exception as exc:
        logger.exception("Extension failed for order %s: %s", order.id, exc)
        if is_reseller_order and reseller_tg_id is not None and deduction > 0.0:
            try:
                await repo.adjust_balance(
                    tg_id=reseller_tg_id,
                    amount=deduction,
                    reason=f"Refund: Extension failed for Order #{order.id}",
                    node_id=0
                )
            except Exception as refund_exc:
                logger.error("Reseller refund failed for reseller %d: %s", reseller_tg_id, refund_exc)
                
        try:
            await bot.send_message(
                order.user_tg_id,
                "⚠️ Payment received but provisioning failed. Our team has been "
                "notified and will sort it out shortly.",
            )
        except Exception:
            pass
        await _notify_admins(
            bot, f"❗️ Addon extension FAILED for order #{order.id}: {exc}"
        )
        return False

    await repo.set_order_status(order.id, OrderStatus.paid)
    user = await repo.get_user(order.user_tg_id)
    lang = user.lang if user and user.lang else "en"
    _ = partial(get_text, lang)
    
    await send_configs(bot, order.user_tg_id, result, title=title, _=_)
    await _notify_admins(
        bot,
        f"✅ Addon #{order.id} applied for user {order.user_tg_id} "
        f"({result.email}).",
        exclude={order.user_tg_id},
    )
    return True


async def _fulfill_upgrade(bot: Bot, order: Order) -> bool:
    node_id = getattr(bot, "node_id", 0)
    is_reseller_order = (node_id > 0)
    deduction = 0.0
    reseller_tg_id = None

    plan = await repo.get_plan(order.plan_id)
    if plan is None:
        logger.error("Upgrade order %s references missing plan %s", order.id, order.plan_id)
        await repo.set_order_status(order.id, OrderStatus.rejected)
        return False

    service = await repo.get_service(order.renew_service_id)
    if service is None:
        logger.error("Upgrade order %s references missing service %s", order.id, order.renew_service_id)
        await repo.set_order_status(order.id, OrderStatus.rejected)
        return False

    if is_reseller_order:
        node = await repo.get_node(node_id)
        if not node:
            logger.error("Fulfill failed: Node bot #%d not found in DB.", node_id)
            return False

        reseller_tg_id = node.owner_tg_id
        reseller = await repo.get_user(reseller_tg_id, 0)
        if not reseller:
            logger.error("Fulfill failed: Reseller owner %d not found in DB.", reseller_tg_id)
            return False

        remaining = await provisioning.compute_remaining(service)
        remaining_gb = remaining.total_bytes / (1024**3)
        
        # Calculate reseller upgrade price
        if plan.traffic_gb == 0:
            new_unlimited_price = await repo.get_reseller_unlimited_price(reseller_tg_id, plan.panel_id)
            old_gb_price = await repo.get_reseller_gb_price(reseller_tg_id, service.panel_id or plan.panel_id)
            deduction = float(new_unlimited_price - (remaining_gb * old_gb_price))
        else:
            gb_price = await repo.get_reseller_gb_price(reseller_tg_id, plan.panel_id)
            deduction = float((plan.traffic_gb - remaining_gb) * gb_price)
        deduction = max(0.0, deduction)

        if reseller.balance < deduction:
            logger.warning("Fulfill delayed: Reseller %d has insufficient balance (%s < %s)", reseller_tg_id, reseller.balance, deduction)
            # Notify reseller
            try:
                await bot.send_message(
                    reseller_tg_id,
                    f"⚠️ <b>Reseller Balance Alert</b>\n\n"
                    f"Your customer (ID: <code>{order.user_tg_id}</code>) tried to upgrade service to plan <b>{plan.title}</b>.\n"
                    f"Required cost: <b>{int(deduction):,} tomans</b>.\n"
                    f"Your balance: <b>{int(reseller.balance):,} tomans</b>.\n\n"
                    f"❌ Order #{order.id} fulfillment failed due to insufficient reseller balance.",
                )
            except Exception:
                pass
            
            # Notify buyer
            try:
                await bot.send_message(
                    order.user_tg_id,
                    "⚠️ <b>Fulfillment delayed</b>\n\n"
                    "Your payment was received but we are experiencing a minor delay in provisioning. Our support team has been notified and will resolve this shortly.",
                )
            except Exception:
                pass
            return False

        # Deduct reseller balance before provisioning
        await repo.adjust_balance(
            tg_id=reseller_tg_id,
            amount=-deduction,
            reason=f"Reseller customer upgrade: {plan.title} (Order #{order.id})",
            node_id=0
        )

    # Apply upgrade to service
    try:
        result = await provisioning.upgrade_service(service, plan)
        title = "Service Upgraded"
    except Exception as exc:
        logger.exception("Upgrade failed for order %s: %s", order.id, exc)
        if is_reseller_order and reseller_tg_id is not None and deduction > 0.0:
            try:
                await repo.adjust_balance(
                    tg_id=reseller_tg_id,
                    amount=deduction,
                    reason=f"Refund: Upgrade failed for Order #{order.id}",
                    node_id=0
                )
            except Exception as refund_exc:
                logger.error("Reseller refund failed for reseller %d: %s", reseller_tg_id, refund_exc)
                
        try:
            await bot.send_message(
                order.user_tg_id,
                "⚠️ Payment received but provisioning failed. Our team has been "
                "notified and will sort it out shortly.",
            )
        except Exception:
            pass
        await _notify_admins(
            bot, f"❗️ Upgrade FAILED for order #{order.id}: {exc}"
        )
        return False

    await repo.set_order_status(order.id, OrderStatus.paid)
    user = await repo.get_user(order.user_tg_id)
    lang = user.lang if user and user.lang else "en"
    _ = partial(get_text, lang)
    
    await send_configs(bot, order.user_tg_id, result, title=title, _=_)
    await _notify_admins(
        bot,
        f"✅ Upgrade #{order.id} applied for user {order.user_tg_id} "
        f"({result.email}).",
        exclude={order.user_tg_id},
    )
    return True
