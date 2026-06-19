from __future__ import annotations

import enum
import time
from typing import List, Optional

from sqlalchemy import BigInteger, Boolean, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.db.base import Base


def _now_ms() -> int:
    return int(time.time() * 1000)


class PaymentMethod(str, enum.Enum):
    card = "card"
    stars = "stars"
    crypto = "crypto"
    manual = "manual"  # admin-granted, no payment
    wallet = "wallet"  # paid from the user's balance


class OrderKind(str, enum.Enum):
    plan = "plan"      # buying/renewing a VPN plan
    topup = "topup"    # charging the user's wallet balance
    trial = "trial"    # free trial service
    extra_gb = "extra_gb"  # buying extra GB for service
    extra_time = "extra_time"  # buying extra Time for service
    upgrade = "upgrade"    # upgrading an existing VPN plan


class OrderStatus(str, enum.Enum):
    pending = "pending"          # awaiting payment / receipt
    awaiting_review = "awaiting_review"  # receipt uploaded, admin to approve
    paid = "paid"               # confirmed, provisioned
    rejected = "rejected"
    cancelled = "cancelled"


class ServiceStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"
    expired = "expired"
    deleted = "deleted"


class Panel(Base):
    """A registered 3X-UI panel the bot can provision clients on."""

    __tablename__ = "panels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(512))
    api_token: Mapped[str] = mapped_column(String(512))
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    sub_base_url: Mapped[str] = mapped_column(String(512), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # When enabled, users can migrate services onto this panel/server.
    allow_migrations: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_trials: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_resellers: Mapped[bool] = mapped_column(Boolean, default=False)
    migration_inbound_ids: Mapped[list] = mapped_column(JSON, default=list)
    trial_inbound_ids: Mapped[list] = mapped_column(JSON, default=list)
    reseller_inbound_ids: Mapped[list] = mapped_column(JSON, default=list)
    reseller_gb_price: Mapped[float] = mapped_column(Float, default=0.0)
    reseller_unlimited_price: Mapped[float] = mapped_column(Float, default=0.0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, default=_now_ms)

    # Middle Server configuration
    use_middle_server: Mapped[bool] = mapped_column(Boolean, default=False)
    middle_server_url: Mapped[str] = mapped_column(String(512), default="")
    middle_server_token: Mapped[str] = mapped_column(String(512), default="")

    plans: Mapped[List["Plan"]] = relationship(back_populates="panel")
    services: Mapped[List["Service"]] = relationship(back_populates="panel")

    @property
    def subscription_base(self) -> str:
        return (self.sub_base_url or self.base_url).rstrip("/")


class User(Base):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    node_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    lang: Mapped[str] = mapped_column(String(8), default="en")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    # Wallet balance, denominated in FIAT_CURRENCY.
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[int] = mapped_column(BigInteger, default=_now_ms)

    # Reseller flags (mainly used on node_id = 0 profile)
    is_reseller: Mapped[bool] = mapped_column(Boolean, default=False)
    reseller_gb_price: Mapped[float] = mapped_column(Float, default=0.0)
    reseller_day_price: Mapped[float] = mapped_column(Float, default=0.0)
    reseller_unlimited_price: Mapped[float] = mapped_column(Float, default=0.0)

    orders: Mapped[List["Order"]] = relationship(
        "Order",
        primaryjoin="and_(User.tg_id == Order.user_tg_id, User.node_id == Order.node_id)",
        foreign_keys="[Order.user_tg_id, Order.node_id]",
        back_populates="user"
    )
    services: Mapped[List["Service"]] = relationship(
        "Service",
        primaryjoin="and_(User.tg_id == Service.user_tg_id, User.node_id == Service.node_id)",
        foreign_keys="[Service.user_tg_id, Service.node_id]",
        back_populates="user"
    )


class ResellerNode(Base):
    __tablename__ = "reseller_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_tg_id: Mapped[int] = mapped_column(BigInteger)
    bot_token: Mapped[str] = mapped_column(String(255), unique=True)
    bot_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    brand_name: Mapped[str] = mapped_column(String(128), default="Reseller Bot")
    support_contact: Mapped[str] = mapped_column(String(128), default="@support")
    card_number: Mapped[str] = mapped_column(String(64), default="")
    card_holder: Mapped[str] = mapped_column(String(128), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=_now_ms)

    owner: Mapped["User"] = relationship(
        "User",
        primaryjoin="and_(ResellerNode.owner_tg_id == User.tg_id, User.node_id == 0)",
        foreign_keys="[ResellerNode.owner_tg_id]"
    )


class ResellerPanelInbound(Base):
    __tablename__ = "reseller_panel_inbounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reseller_tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    panel_id: Mapped[int] = mapped_column(Integer, ForeignKey("panels.id", ondelete="CASCADE"), index=True)
    inbound_ids: Mapped[list] = mapped_column(JSON, default=list)
    reseller_gb_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reseller_unlimited_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")

    traffic_gb: Mapped[int] = mapped_column(Integer, default=0)      # 0 = unlimited
    duration_days: Mapped[int] = mapped_column(Integer, default=30)  # 0 = never expires
    limit_ip: Mapped[int] = mapped_column(Integer, default=0)        # 0 = unlimited devices
    inbound_ids: Mapped[list] = mapped_column(JSON, default=list)
    panel_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("panels.id"), nullable=True
    )

    # Prices per method; None/0 means that method is not offered for this plan.
    price_fiat: Mapped[float] = mapped_column(Float, default=0.0)    # card / manual fiat
    price_usd: Mapped[float] = mapped_column(Float, default=0.0)     # crypto
    price_stars: Mapped[int] = mapped_column(Integer, default=0)     # telegram stars (XTR)

    # Dynamic pricing for extra GB/Time (None = fall back to global settings)
    extra_gb_price_fiat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    extra_gb_price_stars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    extra_gb_price_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    extra_time_price_fiat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    extra_time_price_stars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    extra_time_price_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    extra_gb_mode: Mapped[str] = mapped_column(String(16), default="flexible")
    extra_time_mode: Mapped[str] = mapped_column(String(16), default="flexible")

    extra_gb_packages: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    extra_time_packages: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, default=_now_ms)

    panel: Mapped[Optional["Panel"]] = relationship(back_populates="plans")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_tg_id: Mapped[int] = mapped_column(BigInteger)
    node_id: Mapped[int] = mapped_column(Integer, default=0)
    plan_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("plans.id"), nullable=True)
    # Set when an order renews an existing service rather than creating a new one.
    renew_service_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    extra_gb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    extra_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    # "plan" (default) or "topup" — kept as a plain string for easy migration.
    kind: Mapped[str] = mapped_column(String(16), default=OrderKind.plan.value)
    method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod))
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(16), default="")
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.pending
    )

    provider_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    receipt_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=_now_ms)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=_now_ms, onupdate=_now_ms)

    user: Mapped["User"] = relationship(
        "User",
        primaryjoin="and_(Order.user_tg_id == User.tg_id, Order.node_id == User.node_id)",
        foreign_keys="[Order.user_tg_id, Order.node_id]",
        back_populates="orders"
    )
    plan: Mapped["Plan"] = relationship()


class Service(Base):
    """A provisioned VPN client on the panel, joined by `email`."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_tg_id: Mapped[int] = mapped_column(BigInteger)
    node_id: Mapped[int] = mapped_column(Integer, default=0)
    plan_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("plans.id"), nullable=True
    )
    order_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    sub_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    inbound_ids: Mapped[list] = mapped_column(JSON, default=list)
    panel_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("panels.id"), nullable=True
    )

    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0)   # 0 = unlimited
    expiry_time: Mapped[int] = mapped_column(BigInteger, default=0)   # epoch ms, 0 = never
    status: Mapped[ServiceStatus] = mapped_column(
        Enum(ServiceStatus), default=ServiceStatus.active
    )
    created_at: Mapped[int] = mapped_column(BigInteger, default=_now_ms)

    user: Mapped["User"] = relationship(
        "User",
        primaryjoin="and_(Service.user_tg_id == User.tg_id, Service.node_id == User.node_id)",
        foreign_keys="[Service.user_tg_id, Service.node_id]",
        back_populates="services"
    )
    plan: Mapped[Optional["Plan"]] = relationship()
    panel: Mapped[Optional["Panel"]] = relationship(back_populates="services")


class Transaction(Base):
    """Audit log of every wallet balance change (credit or debit)."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_tg_id: Mapped[int] = mapped_column(BigInteger)
    node_id: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[float] = mapped_column(Float)  # signed: + credit, - debit
    balance_after: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(String(255), default="")
    admin_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    order_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=_now_ms)


class Setting(Base):
    """General settings/config stored in the database."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
