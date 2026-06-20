from __future__ import annotations

from typing import List, Optional, Sequence

from sqlalchemy import func, select

from bot.db.base import async_session_factory
from bot.db.models import (
    Order,
    OrderKind,
    OrderStatus,
    Panel,
    PaymentMethod,
    Plan,
    Service,
    ServiceStatus,
    Transaction,
    User,
    Setting,
    ResellerNode,
    ResellerPanelInbound,
    PromoCode,
)


class InsufficientBalance(Exception):
    """Raised when a wallet debit exceeds the available balance."""


class PanelDeleteError(Exception):
    """Raised when a panel cannot be deleted because it has linked records."""


# ----------------------------- Panels -----------------------------

async def list_panels(only_active: bool = False) -> List[Panel]:
    async with async_session_factory() as session:
        stmt = select(Panel).order_by(Panel.sort_order, Panel.id)
        if only_active:
            stmt = stmt.where(Panel.is_active == True)  # noqa: E712
        return list((await session.execute(stmt)).scalars().all())


async def list_migration_targets(exclude_panel_id: Optional[int] = None) -> List[Panel]:
    """Panels that accept user migrations (active, allow_migrations, have inbounds)."""
    async with async_session_factory() as session:
        stmt = (
            select(Panel)
            .where(Panel.is_active == True)  # noqa: E712
            .where(Panel.allow_migrations == True)  # noqa: E712
            .order_by(Panel.sort_order, Panel.id)
        )
        if exclude_panel_id is not None:
            stmt = stmt.where(Panel.id != exclude_panel_id)
        panels = list((await session.execute(stmt)).scalars().all())
        return [p for p in panels if p.migration_inbound_ids]


async def list_trial_panels() -> List[Panel]:
    """Panels that allow free trials (active, allow_trials, have inbounds)."""
    async with async_session_factory() as session:
        stmt = (
            select(Panel)
            .where(Panel.is_active == True)  # noqa: E712
            .where(Panel.allow_trials == True)  # noqa: E712
            .order_by(Panel.sort_order, Panel.id)
        )
        panels = list((await session.execute(stmt)).scalars().all())
        return [p for p in panels if p.trial_inbound_ids]


async def get_panel(panel_id: int) -> Optional[Panel]:
    async with async_session_factory() as session:
        return await session.get(Panel, panel_id)


async def create_panel(**kwargs) -> Panel:
    async with async_session_factory() as session:
        panel = Panel(**kwargs)
        session.add(panel)
        await session.commit()
        await session.refresh(panel)
        return panel


async def update_panel(panel_id: int, **kwargs) -> Optional[Panel]:
    from bot.panel.client import invalidate_panel_client

    async with async_session_factory() as session:
        panel = await session.get(Panel, panel_id)
        if panel is None:
            return None
        for key, value in kwargs.items():
            setattr(panel, key, value)
        await session.commit()
        await session.refresh(panel)
        invalidate_panel_client(panel_id)
        return panel


async def set_panel_active(panel_id: int, active: bool) -> Optional[Panel]:
    return await update_panel(panel_id, is_active=active)


async def count_panel_links(panel_id: int) -> tuple[int, int]:
    """Return (plan_count, service_count) linked to this panel."""
    async with async_session_factory() as session:
        plan_count = int(
            (
                await session.execute(
                    select(func.count(Plan.id)).where(Plan.panel_id == panel_id)
                )
            ).scalar()
            or 0
        )
        service_count = int(
            (
                await session.execute(
                    select(func.count(Service.id)).where(Service.panel_id == panel_id)
                )
            ).scalar()
            or 0
        )
        return plan_count, service_count


async def count_orphan_services_for_panel(panel_id: int) -> int:
    """Services with null panel_id whose plan is assigned to this panel."""
    async with async_session_factory() as session:
        stmt = (
            select(func.count(Service.id))
            .join(Plan, Service.plan_id == Plan.id)
            .where(Service.panel_id.is_(None))
            .where(Plan.panel_id == panel_id)
        )
        return int((await session.execute(stmt)).scalar() or 0)


async def delete_panel(panel_id: int) -> bool:
    plan_count, service_count = await count_panel_links(panel_id)
    if plan_count or service_count:
        raise PanelDeleteError(
            f"Panel has {plan_count} plan(s) and {service_count} service(s)"
        )
    async with async_session_factory() as session:
        panel = await session.get(Panel, panel_id)
        if panel is None:
            return False
        await session.delete(panel)
        await session.commit()
        return True


async def backfill_service_panel_ids(panel_id: int) -> int:
    """Assign panel_id to orphan services whose plan uses this panel."""
    async with async_session_factory() as session:
        stmt = (
            select(Service)
            .join(Plan, Service.plan_id == Plan.id)
            .where(Service.panel_id.is_(None))
            .where(Plan.panel_id == panel_id)
        )
        services = list((await session.execute(stmt)).scalars().all())
        for service in services:
            service.panel_id = panel_id
        await session.commit()
        return len(services)


# ----------------------------- Users -----------------------------

async def get_or_create_user(
    tg_id: int,
    username: Optional[str] = None,
    full_name: Optional[str] = None,
    is_admin: bool = False,
    node_id: int = 0,
) -> User:
    async with async_session_factory() as session:
        user = await session.get(User, (tg_id, node_id))
        if user is None:
            user = User(
                tg_id=tg_id,
                node_id=node_id,
                username=username,
                full_name=full_name,
                is_admin=is_admin,
            )
            session.add(user)
        else:
            user.username = username or user.username
            user.full_name = full_name or user.full_name
            if is_admin and not user.is_admin:
                user.is_admin = True
        await session.commit()
        await session.refresh(user)
        return user


async def list_user_ids(node_id: int = 0) -> List[int]:
    async with async_session_factory() as session:
        rows = await session.execute(
            select(User.tg_id)
            .where(User.node_id == node_id)
            .where(User.is_blocked == False)
        )
        return [r[0] for r in rows.all()]


async def count_users(node_id: int = 0) -> int:
    async with async_session_factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count(User.tg_id)).where(User.node_id == node_id)
                )
            ).scalar()
            or 0
        )



# ----------------------------- Plans -----------------------------

async def list_plans(only_active: bool = True, node_id: int = 0) -> List[Plan]:
    async with async_session_factory() as session:
        stmt = select(Plan).where(Plan.node_id == node_id)
        if only_active:
            stmt = stmt.where(Plan.is_active == True)  # noqa: E712
        stmt = stmt.order_by(Plan.sort_order, Plan.id)
        return list((await session.execute(stmt)).scalars().all())



async def get_plan(plan_id: int) -> Optional[Plan]:
    async with async_session_factory() as session:
        return await session.get(Plan, plan_id)


async def create_plan(**kwargs) -> Plan:
    async with async_session_factory() as session:
        plan = Plan(**kwargs)
        session.add(plan)
        await session.commit()
        await session.refresh(plan)
        return plan


async def update_plan(plan_id: int, **kwargs) -> Optional[Plan]:
    async with async_session_factory() as session:
        plan = await session.get(Plan, plan_id)
        if plan is None:
            return None
        for key, value in kwargs.items():
            setattr(plan, key, value)
        await session.commit()
        await session.refresh(plan)
        return plan


async def delete_plan(plan_id: int) -> bool:
    async with async_session_factory() as session:
        plan = await session.get(Plan, plan_id)
        if plan is None:
            return False
        await session.delete(plan)
        await session.commit()
        return True


# ----------------------------- Orders -----------------------------

async def create_order(
    user_tg_id: int,
    plan_id: Optional[int],
    method: PaymentMethod,
    amount: float,
    currency: str,
    renew_service_id: Optional[int] = None,
    status: OrderStatus = OrderStatus.pending,
    kind: OrderKind = OrderKind.plan,
    node_id: int = 0,
    extra_gb: Optional[float] = None,
    extra_days: Optional[int] = None,
    quantity: int = 1,
    promo_code: Optional[str] = None,
    discount_amount: float = 0.0,
) -> Order:
    async with async_session_factory() as session:
        order = Order(
            user_tg_id=user_tg_id,
            node_id=node_id,
            plan_id=plan_id,
            method=method,
            amount=amount,
            currency=currency,
            renew_service_id=renew_service_id,
            status=status,
            kind=kind.value if isinstance(kind, OrderKind) else str(kind),
            extra_gb=extra_gb,
            extra_days=extra_days,
            quantity=quantity,
            promo_code=promo_code,
            discount_amount=discount_amount,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        return order



async def get_order(order_id: int) -> Optional[Order]:
    async with async_session_factory() as session:
        return await session.get(Order, order_id)


async def set_order_status(
    order_id: int,
    status: OrderStatus,
    provider_ref: Optional[str] = None,
    receipt_file_id: Optional[str] = None,
) -> Optional[Order]:
    async with async_session_factory() as session:
        order = await session.get(Order, order_id)
        if order is None:
            return None
        order.status = status
        if provider_ref is not None:
            order.provider_ref = provider_ref
        if receipt_file_id is not None:
            order.receipt_file_id = receipt_file_id
        await session.commit()
        await session.refresh(order)
        return order


async def get_order_by_provider_ref(provider_ref: str) -> Optional[Order]:
    async with async_session_factory() as session:
        stmt = select(Order).where(Order.provider_ref == provider_ref)
        return (await session.execute(stmt)).scalars().first()


async def list_pending_review_orders(node_id: int = 0) -> List[Order]:
    async with async_session_factory() as session:
        stmt = (
            select(Order)
            .where(Order.node_id == node_id)
            .where(Order.status == OrderStatus.awaiting_review)
            .order_by(Order.created_at)
        )
        return list((await session.execute(stmt)).scalars().all())


async def count_pending_review_orders(node_id: int = 0) -> int:
    async with async_session_factory() as session:
        stmt = select(func.count(Order.id)).where(
            Order.node_id == node_id,
            Order.status == OrderStatus.awaiting_review
        )
        return int((await session.execute(stmt)).scalar() or 0)


async def list_user_orders(user_tg_id: int, limit: int = 15, node_id: int = 0) -> List[Order]:
    async with async_session_factory() as session:
        stmt = (
            select(Order)
            .where(Order.user_tg_id == user_tg_id)
            .where(Order.node_id == node_id)
            .order_by(Order.created_at.desc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars().all())


async def count_user_trials(user_tg_id: int, node_id: int = 0) -> int:
    """Count how many trial orders a user has."""
    async with async_session_factory() as session:
        stmt = select(func.count(Order.id)).where(
            Order.user_tg_id == user_tg_id,
            Order.node_id == node_id,
            Order.kind == OrderKind.trial.value,
        )
        return int((await session.execute(stmt)).scalar() or 0)


async def get_user(tg_id: int, node_id: int = 0) -> Optional[User]:
    async with async_session_factory() as session:
        return await session.get(User, (tg_id, node_id))


async def set_user_blocked(tg_id: int, blocked: bool, node_id: int = 0) -> Optional[User]:
    async with async_session_factory() as session:
        user = await session.get(User, (tg_id, node_id))
        if user is None:
            return None
        user.is_blocked = blocked
        await session.commit()
        await session.refresh(user)
        return user


async def is_user_blocked(tg_id: int, node_id: int = 0) -> bool:
    async with async_session_factory() as session:
        user = await session.get(User, (tg_id, node_id))
        return bool(user and user.is_blocked)


async def set_user_language(tg_id: int, lang: str, node_id: int = 0) -> Optional[User]:
    async with async_session_factory() as session:
        user = await session.get(User, (tg_id, node_id))
        if user is None:
            return None
        user.lang = lang
        await session.commit()
        await session.refresh(user)
        return user



# ----------------------------- Services -----------------------------

async def create_service(
    user_tg_id: int,
    email: str,
    sub_id: Optional[str],
    inbound_ids: Sequence[int],
    total_bytes: int,
    expiry_time: int,
    plan_id: Optional[int] = None,
    order_id: Optional[int] = None,
    panel_id: Optional[int] = None,
    node_id: int = 0,
) -> Service:
    async with async_session_factory() as session:
        service = Service(
            user_tg_id=user_tg_id,
            node_id=node_id,
            email=email,
            sub_id=sub_id,
            inbound_ids=list(inbound_ids),
            total_bytes=total_bytes,
            expiry_time=expiry_time,
            plan_id=plan_id,
            order_id=order_id,
            panel_id=panel_id,
            status=ServiceStatus.active,
        )
        session.add(service)
        await session.commit()
        await session.refresh(service)
        return service


async def get_service(service_id: int) -> Optional[Service]:
    async with async_session_factory() as session:
        return await session.get(Service, service_id)


async def get_service_by_email(email: str) -> Optional[Service]:
    async with async_session_factory() as session:
        stmt = select(Service).where(Service.email == email)
        return (await session.execute(stmt)).scalars().first()


async def list_user_services(
    user_tg_id: int, include_deleted: bool = False, node_id: int = 0
) -> List[Service]:
    async with async_session_factory() as session:
        stmt = select(Service).where(Service.user_tg_id == user_tg_id).where(Service.node_id == node_id)
        if not include_deleted:
            stmt = stmt.where(Service.status != ServiceStatus.deleted)
        stmt = stmt.order_by(Service.created_at.desc())
        return list((await session.execute(stmt)).scalars().all())


async def update_service(service_id: int, **kwargs) -> Optional[Service]:
    async with async_session_factory() as session:
        service = await session.get(Service, service_id)
        if service is None:
            return None
        for key, value in kwargs.items():
            setattr(service, key, value)
        await session.commit()
        await session.refresh(service)
        return service


async def count_active_services(node_id: Optional[int] = None) -> int:
    async with async_session_factory() as session:
        stmt = select(func.count(Service.id)).where(
            Service.status == ServiceStatus.active
        )
        if node_id is not None:
            stmt = stmt.where(Service.node_id == node_id)
        return int((await session.execute(stmt)).scalar() or 0)



# ----------------------------- Wallet -----------------------------

async def get_balance(tg_id: int, node_id: int = 0) -> float:
    async with async_session_factory() as session:
        user = await session.get(User, (tg_id, node_id))
        return float(user.balance) if user and user.balance else 0.0


async def adjust_balance(
    tg_id: int,
    amount: float,
    reason: str,
    admin_id: Optional[int] = None,
    order_id: Optional[int] = None,
    allow_negative: bool = False,
    node_id: int = 0,
) -> float:
    """Apply a signed delta to a user's balance, logging a Transaction.

    Raises InsufficientBalance when a debit would go below zero (unless
    allow_negative is set). Returns the new balance.
    """
    async with async_session_factory() as session:
        user = await session.get(User, (tg_id, node_id))
        if user is None:
            user = User(tg_id=tg_id, node_id=node_id)
            session.add(user)
            await session.flush()
        current = float(user.balance or 0.0)
        new_balance = current + amount
        if new_balance < 0 and not allow_negative:
            raise InsufficientBalance(
                f"Balance {current:g} is insufficient for {amount:g}"
            )
        user.balance = new_balance
        session.add(
            Transaction(
                user_tg_id=tg_id,
                node_id=node_id,
                amount=amount,
                balance_after=new_balance,
                reason=reason,
                admin_id=admin_id,
                order_id=order_id,
            )
        )
        await session.commit()
        return new_balance


async def list_transactions(tg_id: int, limit: int = 10, node_id: int = 0) -> List[Transaction]:
    async with async_session_factory() as session:
        stmt = (
            select(Transaction)
            .where(Transaction.user_tg_id == tg_id)
            .where(Transaction.node_id == node_id)
            .order_by(Transaction.created_at.desc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars().all())


# ----------------------------- Settings -----------------------------

async def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    async with async_session_factory() as session:
        setting = await session.get(Setting, key)
        return setting.value if setting is not None else default


async def set_setting(key: str, value: str) -> None:
    async with async_session_factory() as session:
        setting = await session.get(Setting, key)
        if setting is None:
            setting = Setting(key=key, value=value)
            session.add(setting)
        else:
            setting.value = value
        await session.commit()


# ----------------------------- Reseller Nodes -----------------------------

async def get_node(node_id: int) -> Optional[ResellerNode]:
    async with async_session_factory() as session:
        return await session.get(ResellerNode, node_id)


async def get_node_by_token(bot_token: str) -> Optional[ResellerNode]:
    async with async_session_factory() as session:
        stmt = select(ResellerNode).where(ResellerNode.bot_token == bot_token)
        return (await session.execute(stmt)).scalars().first()


async def create_node(owner_tg_id: int, bot_token: str, **kwargs) -> ResellerNode:
    async with async_session_factory() as session:
        node = ResellerNode(owner_tg_id=owner_tg_id, bot_token=bot_token, **kwargs)
        session.add(node)
        await session.commit()
        await session.refresh(node)
        return node


async def update_node(node_id: int, **kwargs) -> Optional[ResellerNode]:
    async with async_session_factory() as session:
        node = await session.get(ResellerNode, node_id)
        if node is None:
            return None
        for key, value in kwargs.items():
            setattr(node, key, value)
        await session.commit()
        await session.refresh(node)
        return node


async def list_nodes_for_owner(owner_tg_id: int) -> List[ResellerNode]:
    async with async_session_factory() as session:
        stmt = select(ResellerNode).where(ResellerNode.owner_tg_id == owner_tg_id)
        return list((await session.execute(stmt)).scalars().all())


async def list_all_nodes() -> List[ResellerNode]:
    async with async_session_factory() as session:
        stmt = select(ResellerNode)
        return list((await session.execute(stmt)).scalars().all())


async def list_reseller_panels(only_active: bool = True) -> List[Panel]:
    async with async_session_factory() as session:
        stmt = select(Panel).where(Panel.allow_resellers == True)
        if only_active:
            stmt = stmt.where(Panel.is_active == True)
        stmt = stmt.order_by(Panel.sort_order, Panel.id)
        return list((await session.execute(stmt)).scalars().all())


# ----------------------------- Resellers -----------------------------

async def list_resellers() -> List[User]:
    async with async_session_factory() as session:
        stmt = select(User).where(User.node_id == 0).where(User.is_reseller == True)
        return list((await session.execute(stmt)).scalars().all())


async def promote_to_reseller(
    tg_id: int,
    gb_price: Optional[float] = None,
    day_price: Optional[float] = None,
    unlimited_price: Optional[float] = None,
) -> Optional[User]:
    async with async_session_factory() as session:
        user = await session.get(User, (tg_id, 0))
        if user is None:
            # Create user on main bot context
            user = User(
                tg_id=tg_id,
                node_id=0,
                is_reseller=True,
                reseller_gb_price=gb_price if gb_price is not None else 0.0,
                reseller_day_price=day_price if day_price is not None else 0.0,
                reseller_unlimited_price=unlimited_price if unlimited_price is not None else 0.0,
            )
            session.add(user)
        else:
            user.is_reseller = True
            if gb_price is not None:
                user.reseller_gb_price = gb_price
            if day_price is not None:
                user.reseller_day_price = day_price
            if unlimited_price is not None:
                user.reseller_unlimited_price = unlimited_price
        await session.commit()
        await session.refresh(user)
        return user


async def demote_from_reseller(tg_id: int) -> Optional[User]:
    async with async_session_factory() as session:
        user = await session.get(User, (tg_id, 0))
        if user is not None:
            user.is_reseller = False
        await session.commit()
        if user is not None:
            await session.refresh(user)
        return user


async def get_reseller_panel_inbounds(reseller_tg_id: int, panel_id: int) -> Optional[ResellerPanelInbound]:
    async with async_session_factory() as session:
        stmt = (
            select(ResellerPanelInbound)
            .where(ResellerPanelInbound.reseller_tg_id == reseller_tg_id)
            .where(ResellerPanelInbound.panel_id == panel_id)
        )
        return (await session.execute(stmt)).scalars().first()


async def set_reseller_panel_inbounds(reseller_tg_id: int, panel_id: int, inbound_ids: List[int]) -> None:
    async with async_session_factory() as session:
        stmt = (
            select(ResellerPanelInbound)
            .where(ResellerPanelInbound.reseller_tg_id == reseller_tg_id)
            .where(ResellerPanelInbound.panel_id == panel_id)
        )
        row = (await session.execute(stmt)).scalars().first()
        if not row:
            row = ResellerPanelInbound(
                reseller_tg_id=reseller_tg_id,
                panel_id=panel_id,
                inbound_ids=inbound_ids
            )
            session.add(row)
        else:
            row.inbound_ids = inbound_ids
        await session.commit()


async def list_allowed_inbounds(reseller_tg_id: int, panel_id: int) -> Optional[List[int]]:
    custom = await get_reseller_panel_inbounds(reseller_tg_id, panel_id)
    if custom and custom.inbound_ids:
        return list(custom.inbound_ids)

    panel = await get_panel(panel_id)
    if panel and panel.reseller_inbound_ids:
        return list(panel.reseller_inbound_ids)

    return None


async def get_reseller_gb_price(reseller_tg_id: int, panel_id: Optional[int]) -> float:
    if panel_id is not None:
        custom = await get_reseller_panel_inbounds(reseller_tg_id, panel_id)
        if custom and custom.reseller_gb_price is not None:
            return float(custom.reseller_gb_price)
        panel = await get_panel(panel_id)
        if panel and getattr(panel, "reseller_gb_price", 0.0) > 0:
            return float(panel.reseller_gb_price)
    user = await get_user(reseller_tg_id, 0)
    return float(user.reseller_gb_price) if user else 0.0


_UNSET = object()


async def set_reseller_panel_price(
    reseller_tg_id: int,
    panel_id: int,
    gb_price: Optional[float] = _UNSET,
    unlimited_price: Optional[float] = _UNSET,
) -> None:
    async with async_session_factory() as session:
        stmt = (
            select(ResellerPanelInbound)
            .where(ResellerPanelInbound.reseller_tg_id == reseller_tg_id)
            .where(ResellerPanelInbound.panel_id == panel_id)
        )
        row = (await session.execute(stmt)).scalars().first()
        if not row:
            row = ResellerPanelInbound(
                reseller_tg_id=reseller_tg_id,
                panel_id=panel_id,
                inbound_ids=[],
                reseller_gb_price=gb_price if gb_price is not _UNSET else None,
                reseller_unlimited_price=unlimited_price if unlimited_price is not _UNSET else None
            )
            session.add(row)
        else:
            if gb_price is not _UNSET:
                row.reseller_gb_price = gb_price
            if unlimited_price is not _UNSET:
                row.reseller_unlimited_price = unlimited_price
        await session.commit()


async def get_reseller_unlimited_price(reseller_tg_id: int, panel_id: Optional[int]) -> float:
    if panel_id is not None:
        custom = await get_reseller_panel_inbounds(reseller_tg_id, panel_id)
        if custom and custom.reseller_unlimited_price is not None:
            return float(custom.reseller_unlimited_price)
        panel = await get_panel(panel_id)
        if panel and getattr(panel, "reseller_unlimited_price", 0.0) > 0:
            return float(panel.reseller_unlimited_price)
    user = await get_user(reseller_tg_id, 0)
    return float(user.reseller_unlimited_price) if user else 0.0


async def list_reseller_panel_settings(reseller_tg_id: int) -> List[ResellerPanelInbound]:
    async with async_session_factory() as session:
        stmt = (
            select(ResellerPanelInbound)
            .where(ResellerPanelInbound.reseller_tg_id == reseller_tg_id)
        )
        return list((await session.execute(stmt)).scalars().all())


async def get_income_stats(node_id: Optional[int] = None) -> dict:
    """Calculates income statistics: today, 7 days, 30 days, all time, revenue sources, and popular plans.
    
    If node_id is specified (and > 0), stats are scoped strictly to that reseller node.
    If node_id is 0 or None, stats are aggregated globally for the main admin.
    """
    import time
    from datetime import datetime, timedelta, timezone

    # Calculate timestamps in UTC to avoid local system timezone mismatch
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    today_start_ms = int(today_start.timestamp() * 1000)
    
    seven_days_ago_ms = int((now - timedelta(days=7)).timestamp() * 1000)
    thirty_days_ago_ms = int((now - timedelta(days=30)).timestamp() * 1000)
    
    async with async_session_factory() as session:
        # Base query for all paid orders
        stmt = select(Order).where(Order.status == OrderStatus.paid)
        if node_id is not None and node_id > 0:
            stmt = stmt.where(Order.node_id == node_id)
            
        orders = (await session.execute(stmt)).scalars().all()
        
        # Structure for periods: today, 7d, 30d, all_time
        periods = {
            "today": {},
            "7d": {},
            "30d": {},
            "all_time": {}
        }
        
        # Sources breakdown (only relevant for node_id = 0 or None)
        sources = {
            "main_retail": {},      # node_id = 0, kind = plan
            "reseller_topup": {},    # node_id = 0, kind = topup
            "reseller_retail": {}   # node_id > 0
        }
        
        # Plan popularity counts: plan_id -> { currency -> sales_count }
        plan_counts = {}
        
        for order in orders:
            cur = order.currency or "IRR"
            amt = float(order.amount)
            
            # 1. Period totals
            periods["all_time"][cur] = periods["all_time"].get(cur, 0.0) + amt
            if order.created_at >= thirty_days_ago_ms:
                periods["30d"][cur] = periods["30d"].get(cur, 0.0) + amt
            if order.created_at >= seven_days_ago_ms:
                periods["7d"][cur] = periods["7d"].get(cur, 0.0) + amt
            if order.created_at >= today_start_ms:
                periods["today"][cur] = periods["today"].get(cur, 0.0) + amt
                
            # 2. Source Breakdown (only when querying global main admin)
            if node_id is None or node_id == 0:
                if order.node_id == 0:
                    if order.kind == OrderKind.topup.value:
                        sources["reseller_topup"][cur] = sources["reseller_topup"].get(cur, 0.0) + amt
                    else:
                        sources["main_retail"][cur] = sources["main_retail"].get(cur, 0.0) + amt
                else:
                    sources["reseller_retail"][cur] = sources["reseller_retail"].get(cur, 0.0) + amt
            
            # 3. Plan popularity
            if order.kind in (OrderKind.plan.value, OrderKind.upgrade.value):
                plan_counts[order.plan_id] = plan_counts.get(order.plan_id, {})
                plan_counts[order.plan_id][cur] = plan_counts[order.plan_id].get(cur, 0) + 1
                
        # Resolve plan names
        popular_plans = []
        for p_id, cur_counts in plan_counts.items():
            plan_obj = await get_plan(p_id)
            plan_title = plan_obj.title if plan_obj else f"Plan #{p_id}"
            for cur, count in cur_counts.items():
                popular_plans.append({
                    "plan_id": p_id,
                    "title": plan_title,
                    "currency": cur,
                    "sales_count": count
                })
                
        # Sort plans by sales count descending
        popular_plans.sort(key=lambda x: x["sales_count"], reverse=True)
        
        return {
            "periods": periods,
            "sources": sources,
            "popular_plans": popular_plans[:5]  # Top 5
        }


# ----------------------------- Promo Codes -----------------------------

async def get_promo_code_by_code(code: str, node_id: int = 0) -> Optional[PromoCode]:
    """Fetch an active promo code by its code string (case-insensitive) for a specific bot node."""
    async with async_session_factory() as session:
        stmt = (
            select(PromoCode)
            .where(func.lower(PromoCode.code) == func.lower(code))
            .where(PromoCode.node_id == node_id)
        )
        return (await session.execute(stmt)).scalars().first()


async def get_promo_code(promo_id: int) -> Optional[PromoCode]:
    async with async_session_factory() as session:
        return await session.get(PromoCode, promo_id)


async def create_promo_code(**kwargs) -> PromoCode:
    async with async_session_factory() as session:
        promo = PromoCode(**kwargs)
        # Normalize code to uppercase
        if "code" in kwargs:
            promo.code = kwargs["code"].upper()
        session.add(promo)
        await session.commit()
        await session.refresh(promo)
        return promo


async def list_promo_codes(node_id: int = 0) -> List[PromoCode]:
    async with async_session_factory() as session:
        stmt = (
            select(PromoCode)
            .where(PromoCode.node_id == node_id)
            .order_by(PromoCode.created_at.desc())
        )
        return list((await session.execute(stmt)).scalars().all())


async def update_promo_code(promo_id: int, **kwargs) -> Optional[PromoCode]:
    async with async_session_factory() as session:
        promo = await session.get(PromoCode, promo_id)
        if promo is None:
            return None
        for key, value in kwargs.items():
            if key == "code":
                value = value.upper()
            setattr(promo, key, value)
        await session.commit()
        await session.refresh(promo)
        return promo


async def delete_promo_code(promo_id: int) -> bool:
    async with async_session_factory() as session:
        promo = await session.get(PromoCode, promo_id)
        if promo is None:
            return False
        await session.delete(promo)
        await session.commit()
        return True


async def increment_promo_use(promo_id: int) -> None:
    async with async_session_factory() as session:
        promo = await session.get(PromoCode, promo_id)
        if promo:
            promo.used_count += 1
            await session.commit()


async def has_user_used_promo(user_tg_id: int, promo_code: str, node_id: int = 0) -> bool:
    """Check if a user has already used (or is in the process of paying/reviewing) a specific promo code."""
    async with async_session_factory() as session:
        stmt = (
            select(func.count(Order.id))
            .where(Order.user_tg_id == user_tg_id)
            .where(Order.node_id == node_id)
            .where(func.lower(Order.promo_code) == func.lower(promo_code))
            .where(Order.status.in_([OrderStatus.paid, OrderStatus.awaiting_review]))
        )
        count = (await session.execute(stmt)).scalar() or 0
        return count > 0




