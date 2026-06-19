from __future__ import annotations

import hashlib
import hmac
import json
import logging

from aiogram import Bot
from aiohttp import web

from bot.config import get_settings
from bot.db import repo
from bot.db.models import OrderStatus
from bot.services.fulfillment import fulfill_order

logger = logging.getLogger(__name__)

# NowPayments statuses that mean the money has arrived.
_PAID_STATUSES = {"finished", "confirmed", "sending"}


def _verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """NowPayments signs the sorted JSON body with HMAC-SHA512."""
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    sorted_body = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(
        secret.encode("utf-8"), sorted_body.encode("utf-8"), hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(digest, signature or "")


async def nowpayments_ipn(request: web.Request) -> web.Response:
    settings = get_settings()
    raw = await request.read()
    signature = request.headers.get("x-nowpayments-sig", "")

    if settings.nowpayments_ipn_secret and not _verify_signature(
        raw, signature, settings.nowpayments_ipn_secret
    ):
        logger.warning("Rejected NowPayments IPN: bad signature")
        return web.json_response({"ok": False}, status=401)

    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return web.json_response({"ok": False}, status=400)

    status = str(data.get("payment_status", "")).lower()
    order_id_raw = data.get("order_id")
    logger.info("NowPayments IPN: order=%s status=%s", order_id_raw, status)

    if status not in _PAID_STATUSES:
        return web.json_response({"ok": True})

    order = None
    if order_id_raw is not None:
        try:
            order = await repo.get_order(int(order_id_raw))
        except (TypeError, ValueError):
            order = None
    if order is None:
        invoice_id = str(data.get("invoice_id") or data.get("id") or "")
        if invoice_id:
            order = await repo.get_order_by_provider_ref(invoice_id)

    if order is None:
        logger.warning("IPN: no matching order for %s", order_id_raw)
        return web.json_response({"ok": True})

    if order.status == OrderStatus.paid:
        return web.json_response({"ok": True})

    bot: Bot = request.app["bot"]
    await fulfill_order(bot, order)
    return web.json_response({"ok": True})


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def build_web_app(bot: Bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/nowpayments/ipn", nowpayments_ipn)
    app.router.add_get("/health", health)
    
    # Admin Web-UI API Endpoints
    from bot.web.admin_api import (
        admin_auth, get_stats, list_users, adjust_user_balance, toggle_user_block, toggle_user_reseller,
        list_panels_api, create_panel_api, update_panel_api, delete_panel_api, test_panel_connection,
        list_plans_api, create_plan_api, update_plan_api, delete_plan_api,
        list_orders, approve_order, reject_order, get_order_receipt_file,
        list_nodes_api, create_node_api, update_node_api,
        get_settings_api, update_settings_api, get_user_reseller_panels
    )

    # Auth
    app.router.add_get("/api/admin/auth", admin_auth)
    
    # Stats
    app.router.add_get("/api/admin/stats", get_stats)
    
    # Users
    app.router.add_get("/api/admin/users", list_users)
    app.router.add_post("/api/admin/users/{tg_id}/adjust-balance", adjust_user_balance)
    app.router.add_post("/api/admin/users/{tg_id}/toggle-block", toggle_user_block)
    app.router.add_post("/api/admin/users/{tg_id}/toggle-reseller", toggle_user_reseller)
    app.router.add_get("/api/admin/users/{tg_id}/reseller-panels", get_user_reseller_panels)
    
    # Panels
    app.router.add_get("/api/admin/panels", list_panels_api)
    app.router.add_post("/api/admin/panels", create_panel_api)
    app.router.add_put("/api/admin/panels/{id}", update_panel_api)
    app.router.add_delete("/api/admin/panels/{id}", delete_panel_api)
    app.router.add_post("/api/admin/panels/{id}/test", test_panel_connection)
    
    # Plans
    app.router.add_get("/api/admin/plans", list_plans_api)
    app.router.add_post("/api/admin/plans", create_plan_api)
    app.router.add_put("/api/admin/plans/{id}", update_plan_api)
    app.router.add_delete("/api/admin/plans/{id}", delete_plan_api)
    
    # Orders
    app.router.add_get("/api/admin/orders", list_orders)
    app.router.add_post("/api/admin/orders/{id}/approve", approve_order)
    app.router.add_post("/api/admin/orders/{id}/reject", reject_order)
    app.router.add_get("/api/admin/orders/{id}/receipt", get_order_receipt_file)
    
    # Reseller Nodes
    app.router.add_get("/api/admin/nodes", list_nodes_api)
    app.router.add_post("/api/admin/nodes", create_node_api)
    app.router.add_put("/api/admin/nodes/{id}", update_node_api)
    
    # Settings
    app.router.add_get("/api/admin/settings", get_settings_api)
    app.router.add_put("/api/admin/settings", update_settings_api)
    
    # Static files serving for Web-UI frontend
    import os
    static_path = os.path.join(os.path.dirname(__file__), "static")
    os.makedirs(static_path, exist_ok=True)
    app.router.add_static("/admin", static_path, show_index=True)
    
    return app

