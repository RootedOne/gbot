import logging
import time
import httpx
from aiohttp import web
from sqlalchemy import select, func, or_
from typing import Dict, Any

from bot.config import get_settings
from bot.db import repo
from bot.db.base import async_session_factory
from bot.db.models import (
    User, Plan, Panel, Order, OrderStatus, Service, ResellerNode, Setting, Transaction, ResellerPanelInbound
)
from bot.services.fulfillment import fulfill_order
from bot.panel.client import get_panel_client, PanelError
from bot.web.auth import verify_login_token, create_session, verify_session

logger = logging.getLogger(__name__)

# Helper to require Admin authentication
def admin_required(handler):
    async def wrapper(request: web.Request) -> web.StreamResponse:
        # Check authorization token from header, query string, or cookies
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        else:
            token = request.query.get("token", "")
            
        if not token:
            token = request.cookies.get("session_token", "")
            
        if not token:
            return web.json_response({"error": "Unauthorized"}, status=401)
            
        tg_id = verify_session(token)
        if not tg_id:
            return web.json_response({"error": "Unauthorized or Session Expired"}, status=401)
            
        # Verify the user is an admin
        settings = get_settings()
        if not settings.is_admin(tg_id):
            return web.json_response({"error": "Forbidden: Admin privileges required"}, status=403)
            
        request["admin_tg_id"] = tg_id
        return await handler(request)
    return wrapper

# Auth verification
async def admin_auth(request: web.Request) -> web.Response:
    token = request.query.get("token", "").strip()
    if not token:
        return web.json_response({"error": "Token is required"}, status=400)
        
    tg_id = verify_login_token(token)
    if not tg_id:
        return web.json_response({"error": "Invalid or expired login link"}, status=400)
        
    settings = get_settings()
    if not settings.is_admin(tg_id):
        return web.json_response({"error": "User is not an admin"}, status=403)
        
    session_token = create_session(tg_id)
    response = web.json_response({"ok": True, "session_token": session_token})
    # Set secure session cookie
    response.set_cookie("session_token", session_token, max_age=86400, httponly=True)
    return response

# Stats/Dashboard Overview
@admin_required
async def get_stats(request: web.Request) -> web.Response:
    users_count = await repo.count_users(0)
    active_services = await repo.count_active_services(0)
    pending_receipts = await repo.count_pending_review_orders(0)
    income = await repo.get_income_stats(0)
    
    # Panels status
    panels = await repo.list_panels()
    panel_stats = []
    for p in panels:
        try:
            client = await get_panel_client(p.id)
            srv_status = await client.server_status()
            panel_stats.append({
                "id": p.id,
                "name": p.name,
                "is_active": p.is_active,
                "online": True,
                "cpu": srv_status.get("cpu"),
                "mem": srv_status.get("mem"),
                "xray": (srv_status.get("xray") or {}).get("state", "?")
            })
        except Exception as e:
            panel_stats.append({
                "id": p.id,
                "name": p.name,
                "is_active": p.is_active,
                "online": False,
                "error": str(e)
            })
            
    return web.json_response({
        "stats": {
            "users_count": users_count,
            "active_services": active_services,
            "pending_receipts": pending_receipts
        },
        "income": income,
        "panels": panel_stats
    })

# Users CRUD & Adjustments
@admin_required
async def list_users(request: web.Request) -> web.Response:
    search = request.query.get("search", "").strip()
    limit = int(request.query.get("limit", "50"))
    offset = int(request.query.get("offset", "0"))
    
    async with async_session_factory() as session:
        stmt = select(User).where(User.node_id == 0)
        if search:
            if search.isdigit():
                stmt = stmt.where(or_(User.tg_id == int(search), User.username.ilike(f"%{search}%"), User.full_name.ilike(f"%{search}%")))
            else:
                stmt = stmt.where(or_(User.username.ilike(f"%{search}%"), User.full_name.ilike(f"%{search}%")))
                
        # Get total count
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = await session.execute(count_stmt)
        total_count = total.scalar() or 0
        
        # Paginate
        stmt = stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        users = result.scalars().all()
        
        user_list = []
        for u in users:
            user_list.append({
                "tg_id": u.tg_id,
                "username": u.username,
                "full_name": u.full_name,
                "balance": u.balance,
                "lang": u.lang,
                "is_admin": u.is_admin,
                "is_blocked": u.is_blocked,
                "is_reseller": u.is_reseller,
                "reseller_gb_price": u.reseller_gb_price,
                "reseller_day_price": u.reseller_day_price,
                "reseller_unlimited_price": u.reseller_unlimited_price,
                "created_at": u.created_at
            })
            
    return web.json_response({
        "total": total_count,
        "users": user_list
    })

@admin_required
async def adjust_user_balance(request: web.Request) -> web.Response:
    tg_id = int(request.match_info["tg_id"])
    try:
        data = await request.json()
        amount = float(data.get("amount", 0))
        reason = str(data.get("reason", "Admin Balance Adjustment")).strip()
    except Exception:
        return web.json_response({"error": "Invalid JSON payload"}, status=400)
        
    if amount == 0:
        return web.json_response({"error": "Amount cannot be zero"}, status=400)
        
    try:
        new_balance = await repo.adjust_balance(
            tg_id=tg_id,
            amount=amount,
            reason=reason,
            admin_id=request["admin_tg_id"],
            node_id=0,
            allow_negative=True
        )
        return web.json_response({"ok": True, "new_balance": new_balance})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

@admin_required
async def toggle_user_block(request: web.Request) -> web.Response:
    tg_id = int(request.match_info["tg_id"])
    try:
        data = await request.json()
        blocked = bool(data.get("blocked", False))
    except Exception:
        return web.json_response({"error": "Invalid JSON payload"}, status=400)
        
    user = await repo.set_user_blocked(tg_id=tg_id, blocked=blocked, node_id=0)
    if not user:
        return web.json_response({"error": "User not found"}, status=404)
        
    return web.json_response({"ok": True, "is_blocked": user.is_blocked})

@admin_required
async def toggle_user_reseller(request: web.Request) -> web.Response:
    tg_id = int(request.match_info["tg_id"])
    try:
        data = await request.json()
        is_reseller = bool(data.get("is_reseller", False))
        gb_price = float(data.get("reseller_gb_price", 0.0))
        day_price = float(data.get("reseller_day_price", 0.0))
        unlimited_price = float(data.get("reseller_unlimited_price", 0.0))
        panel_gb_prices = data.get("panel_gb_prices", {})
        panel_unlimited_prices = data.get("panel_unlimited_prices", {})
    except Exception:
        return web.json_response({"error": "Invalid JSON payload"}, status=400)
        
    if is_reseller:
        user = await repo.promote_to_reseller(tg_id, gb_price, day_price, unlimited_price)
        all_panel_ids = set(int(pid) for pid in list(panel_gb_prices.keys()) + list(panel_unlimited_prices.keys()))
        for panel_id in all_panel_ids:
            try:
                g_val = panel_gb_prices.get(str(panel_id))
                u_val = panel_unlimited_prices.get(str(panel_id))
                
                price_gb = float(g_val) if g_val is not None and str(g_val).strip() != "" else None
                price_unl = float(u_val) if u_val is not None and str(u_val).strip() != "" else None
                
                await repo.set_reseller_panel_price(tg_id, panel_id, gb_price=price_gb, unlimited_price=price_unl)
            except Exception as e:
                logger.error("Failed to set reseller panel price for panel %s: %s", panel_id, e)
    else:
        user = await repo.demote_from_reseller(tg_id)
        
    if not user:
        return web.json_response({"error": "User not found"}, status=404)
        
    return web.json_response({
        "ok": True, 
        "is_reseller": user.is_reseller,
        "reseller_gb_price": user.reseller_gb_price,
        "reseller_day_price": user.reseller_day_price,
        "reseller_unlimited_price": user.reseller_unlimited_price
    })

# Panels CRUD & Diagnosis
@admin_required
async def list_panels_api(request: web.Request) -> web.Response:
    panels = await repo.list_panels()
    result = []
    for p in panels:
        result.append({
            "id": p.id,
            "name": p.name,
            "base_url": p.base_url,
            "verify_tls": p.verify_tls,
            "sub_base_url": p.sub_base_url,
            "is_active": p.is_active,
            "allow_migrations": p.allow_migrations,
            "allow_trials": p.allow_trials,
            "allow_resellers": p.allow_resellers,
            "migration_inbound_ids": p.migration_inbound_ids,
            "trial_inbound_ids": p.trial_inbound_ids,
            "reseller_inbound_ids": p.reseller_inbound_ids,
            "reseller_gb_price": getattr(p, "reseller_gb_price", 0.0),
            "reseller_unlimited_price": getattr(p, "reseller_unlimited_price", 0.0),
            "sort_order": p.sort_order
        })
    return web.json_response(result)

@admin_required
async def create_panel_api(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
        
    required = ["name", "base_url", "api_token"]
    if not all(k in data and data[k] for k in required):
        return web.json_response({"error": "Missing required fields (name, base_url, api_token)"}, status=400)
        
    panel = await repo.create_panel(
        name=str(data["name"]),
        base_url=str(data["base_url"]),
        api_token=str(data["api_token"]),
        verify_tls=bool(data.get("verify_tls", True)),
        sub_base_url=str(data.get("sub_base_url", "")),
        is_active=bool(data.get("is_active", True)),
        allow_migrations=bool(data.get("allow_migrations", False)),
        allow_trials=bool(data.get("allow_trials", False)),
        allow_resellers=bool(data.get("allow_resellers", False)),
        migration_inbound_ids=list(data.get("migration_inbound_ids", [])),
        trial_inbound_ids=list(data.get("trial_inbound_ids", [])),
        reseller_inbound_ids=list(data.get("reseller_inbound_ids", [])),
        reseller_gb_price=float(data.get("reseller_gb_price", 0.0)),
        reseller_unlimited_price=float(data.get("reseller_unlimited_price", 0.0)),
        sort_order=int(data.get("sort_order", 0))
    )
    return web.json_response({"ok": True, "id": panel.id})

@admin_required
async def update_panel_api(request: web.Request) -> web.Response:
    panel_id = int(request.match_info["id"])
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
        
    # Build update kwargs
    kwargs = {}
    for key in ["name", "base_url", "api_token", "verify_tls", "sub_base_url", "is_active", 
                "allow_migrations", "allow_trials", "allow_resellers",
                "migration_inbound_ids", "trial_inbound_ids", "reseller_inbound_ids", 
                "reseller_gb_price", "reseller_unlimited_price", "sort_order"]:
        if key in data:
            if key == "sort_order":
                kwargs[key] = int(data[key])
            elif key in ("reseller_gb_price", "reseller_unlimited_price"):
                kwargs[key] = float(data[key])
            elif key in ("verify_tls", "is_active", "allow_migrations", "allow_trials", "allow_resellers"):
                kwargs[key] = bool(data[key])
            elif key in ("migration_inbound_ids", "trial_inbound_ids", "reseller_inbound_ids"):
                kwargs[key] = list(data[key])
            else:
                kwargs[key] = str(data[key])
                
    panel = await repo.update_panel(panel_id, **kwargs)
    if not panel:
        return web.json_response({"error": "Panel not found"}, status=404)
        
    return web.json_response({"ok": True})

@admin_required
async def delete_panel_api(request: web.Request) -> web.Response:
    panel_id = int(request.match_info["id"])
    try:
        success = await repo.delete_panel(panel_id)
        return web.json_response({"ok": success})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

@admin_required
async def test_panel_connection(request: web.Request) -> web.Response:
    panel_id = int(request.match_info["id"])
    try:
        client = await get_panel_client(panel_id)
        srv_status = await client.server_status()
        inbound_options = await client.inbound_options()
        
        inbounds_list = []
        for opt in inbound_options:
            inbounds_list.append({
                "id": opt.id,
                "remark": opt.remark,
                "protocol": opt.protocol,
                "port": opt.port,
                "tls_flow_capable": opt.tls_flow_capable
            })
            
        return web.json_response({
            "ok": True,
            "status": srv_status,
            "inbounds": inbounds_list
        })
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

# Plans CRUD
@admin_required
async def list_plans_api(request: web.Request) -> web.Response:
    plans = await repo.list_plans(only_active=False, node_id=0)
    result = []
    for p in plans:
        result.append({
            "id": p.id,
            "title": p.title,
            "description": p.description,
            "traffic_gb": p.traffic_gb,
            "duration_days": p.duration_days,
            "limit_ip": p.limit_ip,
            "inbound_ids": p.inbound_ids,
            "panel_id": p.panel_id,
            "price_fiat": p.price_fiat,
            "price_usd": p.price_usd,
            "price_stars": p.price_stars,
            "is_active": p.is_active,
            "is_trial": p.is_trial,
            "sort_order": p.sort_order
        })
    return web.json_response(result)

@admin_required
async def create_plan_api(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
        
    if "title" not in data or not data["title"]:
        return web.json_response({"error": "Title is required"}, status=400)
        
    plan = await repo.create_plan(
        node_id=0,
        title=str(data["title"]),
        description=str(data.get("description", "")),
        traffic_gb=int(data.get("traffic_gb", 0)),
        duration_days=int(data.get("duration_days", 30)),
        limit_ip=int(data.get("limit_ip", 0)),
        inbound_ids=list(data.get("inbound_ids", [])),
        panel_id=int(data["panel_id"]) if data.get("panel_id") is not None else None,
        price_fiat=float(data.get("price_fiat", 0.0)),
        price_usd=float(data.get("price_usd", 0.0)),
        price_stars=int(data.get("price_stars", 0)),
        is_active=bool(data.get("is_active", True)),
        is_trial=bool(data.get("is_trial", False)),
        sort_order=int(data.get("sort_order", 0))
    )
    return web.json_response({"ok": True, "id": plan.id})

@admin_required
async def update_plan_api(request: web.Request) -> web.Response:
    plan_id = int(request.match_info["id"])
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
        
    kwargs = {}
    for key in ["title", "description", "traffic_gb", "duration_days", "limit_ip", "inbound_ids", 
                "panel_id", "price_fiat", "price_usd", "price_stars", "is_active", "is_trial", "sort_order"]:
        if key in data:
            if key in ("traffic_gb", "duration_days", "limit_ip", "price_stars", "sort_order"):
                kwargs[key] = int(data[key])
            elif key in ("price_fiat", "price_usd"):
                kwargs[key] = float(data[key])
            elif key in ("is_active", "is_trial"):
                kwargs[key] = bool(data[key])
            elif key == "inbound_ids":
                kwargs[key] = list(data[key])
            elif key == "panel_id":
                kwargs[key] = int(data[key]) if data[key] is not None else None
            else:
                kwargs[key] = str(data[key])
                
    plan = await repo.update_plan(plan_id, **kwargs)
    if not plan:
        return web.json_response({"error": "Plan not found"}, status=404)
        
    return web.json_response({"ok": True})

@admin_required
async def delete_plan_api(request: web.Request) -> web.Response:
    plan_id = int(request.match_info["id"])
    success = await repo.delete_plan(plan_id)
    return web.json_response({"ok": success})

# Orders & Receipt Review
@admin_required
async def list_orders(request: web.Request) -> web.Response:
    status_filter = request.query.get("status", "").strip()
    limit = int(request.query.get("limit", "50"))
    offset = int(request.query.get("offset", "0"))
    
    async with async_session_factory() as session:
        stmt = select(Order).where(Order.node_id == 0)
        if status_filter:
            stmt = stmt.where(Order.status == OrderStatus[status_filter.lower()])
            
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = await session.execute(count_stmt)
        total_count = total.scalar() or 0
        
        stmt = stmt.order_by(Order.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        orders = result.scalars().all()
        
        order_list = []
        for o in orders:
            order_list.append({
                "id": o.id,
                "user_tg_id": o.user_tg_id,
                "plan_id": o.plan_id,
                "plan_title": o.plan.title if o.plan else f"Plan #{o.plan_id}",
                "renew_service_id": o.renew_service_id,
                "kind": o.kind,
                "method": o.method.value if o.method else None,
                "amount": o.amount,
                "currency": o.currency,
                "status": o.status.value if o.status else None,
                "receipt_file_id": o.receipt_file_id,
                "created_at": o.created_at
            })
            
    return web.json_response({
        "total": total_count,
        "orders": order_list
    })

@admin_required
async def approve_order(request: web.Request) -> web.Response:
    order_id = int(request.match_info["id"])
    order = await repo.get_order(order_id)
    if not order:
        return web.json_response({"error": "Order not found"}, status=404)
        
    bot = request.app["bot"]
    success = await fulfill_order(bot, order)
    return web.json_response({"ok": success})

@admin_required
async def reject_order(request: web.Request) -> web.Response:
    order_id = int(request.match_info["id"])
    order = await repo.get_order(order_id)
    if not order:
        return web.json_response({"error": "Order not found"}, status=404)
        
    # Mark rejected
    await repo.set_order_status(order_id, OrderStatus.rejected)
    
    # Notify user in bot
    bot = request.app["bot"]
    try:
        await bot.send_message(
            order.user_tg_id,
            f"❌ Your payment receipt for order <b>#{order.id}</b> was rejected by the admin. "
            "Please contact support if you believe this is a mistake."
        )
    except Exception:
        pass
        
    return web.json_response({"ok": True})

@admin_required
async def get_order_receipt_file(request: web.Request) -> web.StreamResponse:
    order_id = int(request.match_info["id"])
    order = await repo.get_order(order_id)
    if not order or not order.receipt_file_id:
        raise web.HTTPNotFound()
        
    bot = request.app["bot"]
    try:
        file_info = await bot.get_file(order.receipt_file_id)
        url = f"https://api.telegram.org/file/bot{bot.token}/{file_info.file_path}"
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return web.Response(body=resp.content, content_type="image/jpeg")
            else:
                raise web.HTTPInternalServerError(text="Failed to fetch image from Telegram")
    except Exception as e:
        logger.error("Error streaming Telegram receipt image: %s", e)
        raise web.HTTPInternalServerError(text=str(e))

# Reseller Nodes CRUD
@admin_required
async def list_nodes_api(request: web.Request) -> web.Response:
    nodes = await repo.list_all_nodes()
    result = []
    for n in nodes:
        result.append({
            "id": n.id,
            "owner_tg_id": n.owner_tg_id,
            "bot_username": n.bot_username,
            "brand_name": n.brand_name,
            "support_contact": n.support_contact,
            "card_number": n.card_number,
            "card_holder": n.card_holder,
            "is_active": n.is_active,
            "created_at": n.created_at
        })
    return web.json_response(result)

@admin_required
async def create_node_api(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
        
    required = ["owner_tg_id", "bot_token"]
    if not all(k in data and data[k] for k in required):
        return web.json_response({"error": "Missing owner_tg_id or bot_token"}, status=400)
        
    try:
        node = await repo.create_node(
            owner_tg_id=int(data["owner_tg_id"]),
            bot_token=str(data["bot_token"]),
            brand_name=str(data.get("brand_name", "Reseller Bot")),
            support_contact=str(data.get("support_contact", "@support")),
            card_number=str(data.get("card_number", "")),
            card_holder=str(data.get("card_holder", "")),
            is_active=bool(data.get("is_active", True))
        )
        return web.json_response({"ok": True, "id": node.id})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

@admin_required
async def update_node_api(request: web.Request) -> web.Response:
    node_id = int(request.match_info["id"])
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
        
    kwargs = {}
    for key in ["brand_name", "support_contact", "card_number", "card_holder", "is_active"]:
        if key in data:
            if key == "is_active":
                kwargs[key] = bool(data[key])
            else:
                kwargs[key] = str(data[key])
                
    node = await repo.update_node(node_id, **kwargs)
    if not node:
        return web.json_response({"error": "Node not found"}, status=404)
        
    # Update settings cache for the node
    from bot.config import update_node_cache
    update_node_cache(
        node_id=node.id,
        owner_tg_id=node.owner_tg_id,
        brand_name=node.brand_name,
        support_contact=node.support_contact,
        card_number=node.card_number,
        card_holder=node.card_holder
    )
    return web.json_response({"ok": True})

# System Settings Retrieve & Edit
@admin_required
async def get_settings_api(request: web.Request) -> web.Response:
    s = get_settings()
    return web.json_response({
        "brand_name": s.brand_name,
        "support_contact": s.support_contact,
        "fiat_currency": s.fiat_currency,
        "card_number": s.card_number,
        "card_holder": s.card_holder,
        "stars_enabled": s.stars_enabled,
        "crypto_enabled": s.crypto_enabled,
        "nowpayments_api_key": s.nowpayments_api_key,
        "nowpayments_ipn_secret": s.nowpayments_ipn_secret,
        "public_base_url": s.public_base_url,
        "extra_gb_price_fiat": s.extra_gb_price_fiat,
        "extra_gb_price_stars": s.extra_gb_price_stars,
        "extra_gb_price_usd": s.extra_gb_price_usd,
        "extra_time_price_fiat": s.extra_time_price_fiat,
        "extra_time_price_stars": s.extra_time_price_stars,
        "extra_time_price_usd": s.extra_time_price_usd,
        "admin_ids": s.admin_ids
    })

@admin_required
async def update_settings_api(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
        
    s = get_settings()
    for key, val in data.items():
        # Validate and update key
        if key in ("stars_enabled", "crypto_enabled"):
            new_val = bool(val)
            await repo.set_setting(key, "true" if new_val else "false")
            setattr(s, key, new_val)
        elif key in ("extra_gb_price_stars", "extra_time_price_stars"):
            new_val = int(val)
            await repo.set_setting(key, str(new_val))
            setattr(s, key, new_val)
        elif key in ("extra_gb_price_fiat", "extra_gb_price_usd", "extra_time_price_fiat", "extra_time_price_usd"):
            new_val = float(val)
            await repo.set_setting(key, str(new_val))
            setattr(s, key, new_val)
        elif key in ("brand_name", "support_contact", "fiat_currency", "card_number", "card_holder", 
                     "nowpayments_api_key", "nowpayments_ipn_secret", "public_base_url"):
            new_val = str(val).strip()
            await repo.set_setting(key, new_val)
            setattr(s, key, new_val)
        elif key == "admin_ids":
            # List of integers
            ids = [int(i) for i in val if str(i).isdigit()]
            # Lockout guard
            if request["admin_tg_id"] not in ids:
                return web.json_response({"error": "Lockout guard: You must include your own Telegram ID in the Admin list"}, status=400)
            ids_str = ",".join(str(i) for i in ids)
            await repo.set_setting("admin_ids_raw", ids_str)
            setattr(s, "admin_ids_raw", ids_str)
            
    return web.json_response({"ok": True})


@admin_required
async def get_user_reseller_panels(request: web.Request) -> web.Response:
    tg_id = int(request.match_info["tg_id"])
    
    panels = await repo.list_reseller_panels(only_active=True)
    custom_settings = await repo.list_reseller_panel_settings(tg_id)
    price_map = {c.panel_id: c.reseller_gb_price for c in custom_settings}
    unl_price_map = {c.panel_id: c.reseller_unlimited_price for c in custom_settings}
    
    result = []
    for p in panels:
        result.append({
            "panel_id": p.id,
            "panel_name": p.name,
            "gb_price": price_map.get(p.id),
            "unlimited_price": unl_price_map.get(p.id)
        })
        
    return web.json_response(result)
