from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from typing import List, Optional

from bot.db import repo
from bot.db.models import Panel, Plan, Service, ServiceStatus
from bot.panel.client import PanelClient, PanelError, get_panel_client
from bot.panel.schemas import ClientInfo, ClientTraffic
from bot.utils.format import days_from_now_ms, gb_to_bytes, now_ms

logger = logging.getLogger(__name__)


@dataclass
class ProvisionResult:
    service: Service
    links: List[str]
    sub_url: Optional[str]
    email: str


class ProvisioningError(Exception):
    """Raised when a plan or service has no valid panel assignment."""


@dataclass
class RemainingQuota:
    total_bytes: int  # 0 = unlimited
    expiry_time: int  # epoch ms; 0 = never
    limit_ip: int


def make_email(tg_id: int, plan_id: int) -> str:
    """Generate a unique, stable email key used to join bot <-> panel."""
    suffix = secrets.token_hex(3)
    return f"u{tg_id}-p{plan_id}-{int(time.time())}{suffix[:2]}"


def make_migration_email(tg_id: int, service_id: int) -> str:
    suffix = secrets.token_hex(2)
    return f"u{tg_id}-m{service_id}-{int(time.time())}{suffix}"


async def compute_remaining(service: Service) -> RemainingQuota:
    """Compute remaining traffic quota and expiry for a service."""
    traffic = await fetch_traffic(service)
    plan = await repo.get_plan(service.plan_id) if service.plan_id else None

    total = service.total_bytes or 0
    if traffic and traffic.total:
        total = int(traffic.total)
    used = int(traffic.used) if traffic else 0

    if not total:
        remaining_bytes = 0
    else:
        remaining_bytes = max(0, total - used)

    expiry = service.expiry_time or 0
    if traffic and traffic.expiry_time:
        expiry = int(traffic.expiry_time)

    limit_ip = int(plan.limit_ip) if plan else 0
    if service.panel_id:
        try:
            panel = await get_panel_client(service.panel_id)
            info = await panel.get_client(service.email)
            if info and info.limit_ip:
                limit_ip = info.limit_ip
        except (PanelError, ProvisioningError):
            pass

    return RemainingQuota(
        total_bytes=remaining_bytes,
        expiry_time=expiry,
        limit_ip=limit_ip,
    )


def has_remaining_quota(remaining: RemainingQuota) -> bool:
    unlimited_data = remaining.total_bytes == 0
    unlimited_time = remaining.expiry_time == 0
    has_data = unlimited_data or remaining.total_bytes > 0
    has_time = unlimited_time or remaining.expiry_time > now_ms()
    return has_data and has_time


from urllib.parse import urlparse


def build_sub_url(sub_id: Optional[str], panel: Panel) -> Optional[str]:
    if not sub_id:
        return None
    raw_bases = panel.sub_base_url or panel.base_url
    bases = [b.strip().rstrip("/") for b in raw_bases.replace("\n", ",").split(",") if b.strip()]

    urls = []
    for base in bases:
        parsed = urlparse(base)
        path = parsed.path.strip("/")
        
        # If there is a path component, do not append "/sub/"
        if path:
            urls.append(f"{base}/{sub_id}")
        else:
            urls.append(f"{base}/sub/{sub_id}")

    return "\n".join(urls)




async def _resolve_panel_for_plan(plan: Plan) -> Panel:
    if not plan.panel_id:
        raise ProvisioningError(
            "This plan has no panel assigned. Ask an admin to set one in Plans."
        )
    panel = await repo.get_panel(plan.panel_id)
    if panel is None:
        raise ProvisioningError(f"Panel #{plan.panel_id} no longer exists.")
    if not panel.is_active:
        raise ProvisioningError(
            f"Panel “{panel.name}” is inactive. Ask an admin to re-enable it."
        )
    return panel


async def _resolve_panel_for_service(service: Service) -> Panel:
    if not service.panel_id:
        raise ProvisioningError(
            f"Service {service.email} has no panel assigned. "
            "Ask an admin to backfill it from Panels."
        )
    panel = await repo.get_panel(service.panel_id)
    if panel is None:
        raise ProvisioningError(f"Panel #{service.panel_id} no longer exists.")
    return panel


async def _fetch_sub_id(
    panel: PanelClient, service: Service
) -> Optional[str]:
    """Return subId from DB or refresh it from the panel."""
    if service.sub_id:
        return service.sub_id
    try:
        info = await panel.get_client(service.email)
    except PanelError as exc:
        logger.warning("get_client failed for %s: %s", service.email, exc)
        return None
    sub_id = info.sub_id if info else None
    if sub_id:
        await repo.update_service(service.id, sub_id=sub_id)
    return sub_id


async def _resolve_links_and_sub(
    panel: PanelClient,
    panel_row: Panel,
    email: str,
    *,
    service: Optional[Service] = None,
) -> tuple[List[str], Optional[str], Optional[str]]:
    """Return (links, sub_id, sub_url) for a freshly created/updated client."""
    info: Optional[ClientInfo] = None
    try:
        info = await panel.get_client(email)
    except PanelError as exc:
        logger.warning("get_client failed for %s: %s", email, exc)

    sub_id = info.sub_id if info else None
    if not sub_id and service is not None:
        sub_id = service.sub_id
    try:
        links = await panel.client_links(email)
    except PanelError as exc:
        logger.warning("client_links failed for %s: %s", email, exc)
        links = []

    return links, sub_id, build_sub_url(sub_id, panel_row)


async def provision_for_plan(
    user_tg_id: int,
    plan: Plan,
    order_id: Optional[int] = None,
) -> ProvisionResult:
    """Create a brand-new VPN client on the panel for the given plan."""
    panel_row = await _resolve_panel_for_plan(plan)
    panel = await get_panel_client(panel_row.id)
    email = make_email(user_tg_id, plan.id)
    total_bytes = gb_to_bytes(plan.traffic_gb) if plan.traffic_gb else 0
    expiry = days_from_now_ms(plan.duration_days)

    await panel.add_client(
        email=email,
        inbound_ids=list(plan.inbound_ids or []),
        total_gb_bytes=total_bytes,
        expiry_time_ms=expiry,
        tg_id=user_tg_id,
        limit_ip=plan.limit_ip or 0,
        enable=True,
    )

    links, sub_id, sub_url = await _resolve_links_and_sub(panel, panel_row, email)

    node_id = getattr(plan, "node_id", 0)
    service = await repo.create_service(
        user_tg_id=user_tg_id,
        email=email,
        sub_id=sub_id,
        inbound_ids=list(plan.inbound_ids or []),
        total_bytes=total_bytes,
        expiry_time=expiry,
        plan_id=plan.id,
        order_id=order_id,
        panel_id=panel_row.id,
        node_id=node_id,
    )

    return ProvisionResult(service=service, links=links, sub_url=sub_url, email=email)


async def provision_custom_package(
    user_tg_id: int,
    plan: Plan,
    traffic_gb: int,
    duration_days: int,
    limit_ip: int,
    order_id: Optional[int] = None,
) -> ProvisionResult:
    """Create a brand-new VPN client on the panel with custom parameters cloned from a base plan."""
    panel_row = await _resolve_panel_for_plan(plan)
    panel = await get_panel_client(panel_row.id)
    email = make_email(user_tg_id, plan.id)
    total_bytes = gb_to_bytes(traffic_gb) if traffic_gb else 0
    expiry = days_from_now_ms(duration_days)

    await panel.add_client(
        email=email,
        inbound_ids=list(plan.inbound_ids or []),
        total_gb_bytes=total_bytes,
        expiry_time_ms=expiry,
        tg_id=user_tg_id,
        limit_ip=limit_ip,
        enable=True,
    )

    links, sub_id, sub_url = await _resolve_links_and_sub(panel, panel_row, email)

    node_id = getattr(plan, "node_id", 0)
    service = await repo.create_service(
        user_tg_id=user_tg_id,
        email=email,
        sub_id=sub_id,
        inbound_ids=list(plan.inbound_ids or []),
        total_bytes=total_bytes,
        expiry_time=expiry,
        plan_id=None,
        order_id=order_id,
        panel_id=panel_row.id,
        node_id=node_id,
    )

    return ProvisionResult(service=service, links=links, sub_url=sub_url, email=email)


async def provision_trial_service(
    user_tg_id: int,
    panel_id: int,
    node_id: int = 0,
) -> ProvisionResult:
    """Create a free trial VPN client on the chosen panel."""
    panel_row = await repo.get_panel(panel_id)
    if not panel_row or not panel_row.is_active or not panel_row.allow_trials:
        raise ProvisioningError("This panel is no longer available for trials.")
        
    panel = await get_panel_client(panel_id)
    # Give a unique ID to avoid collisions (e.g. user might take 2 trials)
    trial_id = int(time.time() * 1000) % 100000
    email = f"u{user_tg_id}-trial-{trial_id}"
    
    total_bytes = int(0.2 * 1024 * 1024 * 1024) # 0.2 GB
    expiry = now_ms() + (3600 * 1000) # 1 hour
    
    # Use trial inbounds
    inbounds = list(panel_row.trial_inbound_ids or [])
    if not inbounds:
        raise ProvisioningError("No trial inbounds are configured for this server.")
 
    await panel.add_client(
        email=email,
        inbound_ids=inbounds,
        total_gb_bytes=total_bytes,
        expiry_time_ms=expiry,
        tg_id=user_tg_id,
        limit_ip=1, # limit to 1 IP for trial
        enable=True,
    )
 
    links, sub_id, sub_url = await _resolve_links_and_sub(panel, panel_row, email)
 
    from bot.db.models import OrderKind, OrderStatus, PaymentMethod
 
    order = await repo.create_order(
        user_tg_id=user_tg_id,
        plan_id=0,
        method=PaymentMethod.manual,
        amount=0.0,
        currency="USD",
        status=OrderStatus.paid,
        kind=OrderKind.trial.value,
        node_id=node_id,
    )
 
    service = await repo.create_service(
        user_tg_id=user_tg_id,
        email=email,
        sub_id=sub_id,
        inbound_ids=inbounds,
        total_bytes=total_bytes,
        expiry_time=expiry,
        plan_id=None,
        order_id=order.id,
        panel_id=panel_id,
        node_id=node_id,
    )

    return ProvisionResult(service=service, links=links, sub_url=sub_url, email=email)


async def renew_service(service: Service, plan: Plan) -> ProvisionResult:
    """Extend expiry and top up quota on an existing client.

    The panel `update` replaces the whole row, so we read the current client,
    merge the new totals, and write it back.
    """
    panel_row = await _resolve_panel_for_service(service)
    panel = await get_panel_client(panel_row.id)
    email = service.email

    current = await panel.get_client(email)

    # Extend from the later of (now, current expiry) so unused time isn't lost.
    base_expiry = service.expiry_time or (current.expiry_time if current else 0)
    if base_expiry and base_expiry > now_ms():
        new_expiry = base_expiry + plan.duration_days * 86400 * 1000 if plan.duration_days else 0
    else:
        new_expiry = days_from_now_ms(plan.duration_days)

    add_bytes = gb_to_bytes(plan.traffic_gb) if plan.traffic_gb else 0
    if not service.total_bytes or not add_bytes:
        # Either side unlimited -> result unlimited only if plan is unlimited;
        # otherwise reset to the plan's quota.
        new_total = 0 if not add_bytes else add_bytes
    else:
        new_total = service.total_bytes + add_bytes

    payload: dict = {
        "email": email,
        "totalGB": new_total,
        "expiryTime": new_expiry,
        "enable": True,
        "tgId": service.user_tg_id,
        "limitIp": plan.limit_ip or 0,
    }
    if current:
        # preserve secrets / subId / uuid if present
        for key in ("id", "uuid", "subId", "flow", "password", "auth", "security"):
            val = current.raw.get(key)
            if val is not None:
                if key in ("id", "uuid"):
                    payload[key] = str(current.uuid) if current.uuid else str(val)
                elif key == "subId":
                    payload[key] = str(val)
                else:
                    payload[key] = val

    await panel.update_client(email, payload)

    links, sub_id, sub_url = await _resolve_links_and_sub(panel, panel_row, email)

    await repo.update_service(
        service.id,
        total_bytes=new_total,
        expiry_time=new_expiry,
        sub_id=sub_id or service.sub_id,
        status=ServiceStatus.active,
    )
    refreshed = await repo.get_service(service.id)
    return ProvisionResult(
        service=refreshed or service, links=links, sub_url=sub_url, email=email
    )


async def upgrade_service(service: Service, plan: Plan) -> ProvisionResult:
    """Upgrade an existing client to a new plan.

    Resets the client's quota and expiry on the panel to match the new plan's parameters,
    since the remaining value of the old plan has been deducted as a discount.
    """
    panel_row = await _resolve_panel_for_service(service)
    panel = await get_panel_client(panel_row.id)
    email = service.email

    current = await panel.get_client(email)

    new_expiry = days_from_now_ms(plan.duration_days) if plan.duration_days else 0
    new_total = gb_to_bytes(plan.traffic_gb) if plan.traffic_gb else 0

    payload: dict = {
        "email": email,
        "totalGB": new_total,
        "expiryTime": new_expiry,
        "enable": True,
        "tgId": service.user_tg_id,
        "limitIp": plan.limit_ip or 0,
    }
    if current:
        for key in ("id", "uuid", "subId", "flow", "password", "auth", "security"):
            val = current.raw.get(key)
            if val is not None:
                if key in ("id", "uuid"):
                    payload[key] = str(current.uuid) if current.uuid else str(val)
                elif key == "subId":
                    payload[key] = str(val)
                else:
                    payload[key] = val

    await panel.update_client(email, payload)

    links, sub_id, sub_url = await _resolve_links_and_sub(panel, panel_row, email)

    await repo.update_service(
        service.id,
        plan_id=plan.id,
        total_bytes=new_total,
        expiry_time=new_expiry,
        sub_id=sub_id or service.sub_id,
        status=ServiceStatus.active,
    )
    refreshed = await repo.get_service(service.id)
    return ProvisionResult(
        service=refreshed or service, links=links, sub_url=sub_url, email=email
    )


async def extend_service(
    service: Service, add_days: int, add_gb: int = 0
) -> ProvisionResult:
    """Admin helper: add days and optionally top up GB without resetting quota.

    `add_gb == 0` leaves the existing quota untouched (days-only extension).
    """
    panel_row = await _resolve_panel_for_service(service)
    panel = await get_panel_client(panel_row.id)
    email = service.email
    current = await panel.get_client(email)

    base_expiry = service.expiry_time or (current.expiry_time if current else 0)
    if add_days:
        if base_expiry and base_expiry > now_ms():
            new_expiry = base_expiry + add_days * 86400 * 1000
        else:
            new_expiry = days_from_now_ms(add_days)
    else:
        new_expiry = base_expiry

    if add_gb > 0 and service.total_bytes:
        new_total = service.total_bytes + gb_to_bytes(add_gb)
    elif add_gb > 0 and not service.total_bytes:
        # service was unlimited -> stays unlimited
        new_total = 0
    else:
        new_total = service.total_bytes

    payload: dict = {
        "email": email,
        "totalGB": new_total,
        "expiryTime": new_expiry,
        "enable": True,
        "tgId": service.user_tg_id,
    }
    if current:
        for key in ("id", "uuid", "subId", "flow", "password", "auth", "limitIp"):
            val = current.raw.get(key)
            if val is not None:
                if key in ("id", "uuid"):
                    payload[key] = str(current.uuid) if current.uuid else str(val)
                elif key == "subId":
                    payload[key] = str(val)
                else:
                    payload[key] = val

    await panel.update_client(email, payload)
    links, sub_id, sub_url = await _resolve_links_and_sub(panel, panel_row, email)
    await repo.update_service(
        service.id,
        total_bytes=new_total,
        expiry_time=new_expiry,
        sub_id=sub_id or service.sub_id,
        status=ServiceStatus.active,
    )
    refreshed = await repo.get_service(service.id)
    return ProvisionResult(
        service=refreshed or service, links=links, sub_url=sub_url, email=email
    )


async def set_service_enabled(service: Service, enabled: bool) -> bool:
    """Enable/disable a client by patching its row through update."""
    panel_row = await _resolve_panel_for_service(service)
    panel = await get_panel_client(panel_row.id)
    current = await panel.get_client(service.email)
    payload: dict = {
        "email": service.email,
        "totalGB": service.total_bytes,
        "expiryTime": service.expiry_time,
        "enable": enabled,
        "tgId": service.user_tg_id,
    }
    if current:
        for key in ("id", "uuid", "subId", "flow", "password", "auth", "limitIp"):
            val = current.raw.get(key)
            if val is not None:
                if key in ("id", "uuid"):
                    payload[key] = str(current.uuid) if current.uuid else str(val)
                elif key == "subId":
                    payload[key] = str(val)
                else:
                    payload[key] = val
    await panel.update_client(service.email, payload)
    await repo.update_service(
        service.id,
        status=ServiceStatus.active if enabled else ServiceStatus.disabled,
    )
    return True


async def delete_service(service: Service) -> bool:
    panel_row = await _resolve_panel_for_service(service)
    panel = await get_panel_client(panel_row.id)
    try:
        await panel.delete_client(service.email)
    except PanelError as exc:
        logger.warning("delete_client failed for %s: %s", service.email, exc)
    await repo.update_service(service.id, status=ServiceStatus.deleted)
    return True


import uuid

async def regenerate_client(service: Service) -> ProvisionResult:
    panel_row = await _resolve_panel_for_service(service)
    panel = await get_panel_client(panel_row.id)
    email = service.email
    current = await panel.get_client(email)
    
    new_uuid = str(uuid.uuid4())
    new_sub_id = secrets.token_hex(8)
    
    payload: dict = {
        "email": email,
        "totalGB": service.total_bytes,
        "expiryTime": service.expiry_time,
        "enable": service.status == ServiceStatus.active,
        "tgId": service.user_tg_id,
        "uuid": new_uuid,
        "id": new_uuid,
        "subId": new_sub_id,
    }
    
    if current:
        for key in ("limitIp", "flow", "password", "auth", "security"):
            if current.raw.get(key) is not None:
                payload[key] = current.raw.get(key)
    
    await panel.update_client(email, payload)
    
    links, sub_id, sub_url = await _resolve_links_and_sub(panel, panel_row, email)
    
    await repo.update_service(
        service.id,
        sub_id=new_sub_id,
    )
    
    refreshed = await repo.get_service(service.id)
    return ProvisionResult(
        service=refreshed or service, links=links, sub_url=sub_url, email=email
    )


async def fetch_traffic(service: Service) -> Optional[ClientTraffic]:
    try:
        panel_row = await _resolve_panel_for_service(service)
        panel = await get_panel_client(panel_row.id)
        return await panel.client_traffic(service.email)
    except (PanelError, ProvisioningError) as exc:
        logger.warning("client_traffic failed for %s: %s", service.email, exc)
        return None


async def can_migrate_service(service: Service) -> bool:
    if service.status != ServiceStatus.active or not service.panel_id:
        return False
    targets = await repo.list_migration_targets(exclude_panel_id=service.panel_id)
    if not targets:
        return False
    remaining = await compute_remaining(service)
    return has_remaining_quota(remaining)


async def migrate_service(service: Service, target_panel_id: int) -> ProvisionResult:
    """Move a service to another panel, preserving remaining quota and expiry."""
    if service.status != ServiceStatus.active:
        raise ProvisioningError("Only active services can be migrated.")

    if not service.panel_id:
        raise ProvisioningError("Service has no source panel assigned.")

    if service.panel_id == target_panel_id:
        raise ProvisioningError("Service is already on this server.")

    target_row = await repo.get_panel(target_panel_id)
    if target_row is None:
        raise ProvisioningError("Target server not found.")
    if not target_row.is_active:
        raise ProvisioningError("Target server is inactive.")
    if not target_row.allow_migrations:
        raise ProvisioningError("This server does not accept migrations.")

    inbound_ids = list(target_row.migration_inbound_ids or [])
    if not inbound_ids:
        raise ProvisioningError(
            "Target server is not configured for migrations (no inbounds)."
        )

    remaining = await compute_remaining(service)
    if not has_remaining_quota(remaining):
        raise ProvisioningError(
            "No remaining traffic or time left on this service to migrate."
        )

    source_row = await _resolve_panel_for_service(service)
    source_panel = await get_panel_client(source_row.id)
    target_panel = await get_panel_client(target_panel_id)

    new_email = make_migration_email(service.user_tg_id, service.id)
    old_email = service.email

    await target_panel.add_client(
        email=new_email,
        inbound_ids=inbound_ids,
        total_gb_bytes=remaining.total_bytes,
        expiry_time_ms=remaining.expiry_time,
        tg_id=service.user_tg_id,
        limit_ip=remaining.limit_ip,
        enable=True,
    )

    try:
        await source_panel.delete_client(old_email)
    except PanelError as exc:
        logger.warning(
            "delete_client failed during migration for %s: %s", old_email, exc
        )

    links, sub_id, sub_url = await _resolve_links_and_sub(
        target_panel, target_row, new_email
    )

    await repo.update_service(
        service.id,
        email=new_email,
        panel_id=target_panel_id,
        inbound_ids=inbound_ids,
        total_bytes=remaining.total_bytes,
        expiry_time=remaining.expiry_time,
        sub_id=sub_id,
        status=ServiceStatus.active,
    )
    refreshed = await repo.get_service(service.id)
    return ProvisionResult(
        service=refreshed or service,
        links=links,
        sub_url=sub_url,
        email=new_email,
    )


async def fetch_links(service: Service) -> tuple[List[str], Optional[str]]:
    try:
        panel_row = await _resolve_panel_for_service(service)
        panel = await get_panel_client(panel_row.id)
        links = await panel.client_links(service.email)
        sub_id = await _fetch_sub_id(panel, service)
        return links, build_sub_url(sub_id, panel_row)
    except (PanelError, ProvisioningError) as exc:
        logger.warning("client_links failed for %s: %s", service.email, exc)
        return [], None
