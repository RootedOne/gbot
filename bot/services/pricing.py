from __future__ import annotations

from typing import List, Optional, Tuple

from bot.config import get_settings
from bot.db.models import PaymentMethod, Plan


def available_methods(plan: Plan) -> List[PaymentMethod]:
    """Which payment methods are offered for this plan given config + prices."""
    settings = get_settings()
    methods: List[PaymentMethod] = []
    if plan.price_fiat and plan.price_fiat > 0 and settings.card_number:
        methods.append(PaymentMethod.card)
    if plan.price_stars and plan.price_stars > 0 and settings.stars_enabled:
        methods.append(PaymentMethod.stars)
    if plan.price_usd and plan.price_usd > 0 and settings.crypto_enabled:
        methods.append(PaymentMethod.crypto)
    return methods


def amount_for(plan: Plan, method: PaymentMethod) -> Tuple[float, str]:
    """Return (amount, currency_label) for a plan + method."""
    settings = get_settings()
    if method == PaymentMethod.card:
        return float(plan.price_fiat), settings.fiat_currency
    if method == PaymentMethod.stars:
        return float(plan.price_stars), "XTR"
    if method == PaymentMethod.crypto:
        return float(plan.price_usd), "USD"
    return 0.0, settings.fiat_currency


def price_summary(plan: Plan) -> str:
    """Human-readable price line for plan listings."""
    settings = get_settings()
    parts: List[str] = []
    if plan.price_fiat:
        parts.append(f"{int(plan.price_fiat):,} {settings.fiat_currency}")
    if plan.price_stars:
        parts.append(f"⭐ {plan.price_stars}")
    if plan.price_usd:
        parts.append(f"${plan.price_usd:g}")
    return " | ".join(parts) if parts else "—"


def plan_caption(plan: Plan) -> str:
    gb = "Unlimited" if not plan.traffic_gb else f"{plan.traffic_gb} GB"
    days = "Never expires" if not plan.duration_days else f"{plan.duration_days} days"
    ips = "Unlimited devices" if not plan.limit_ip else f"{plan.limit_ip} devices"
    lines = [
        f"<b>{plan.title}</b>",
        plan.description or "",
        "",
        f"📦 Traffic: {gb}",
        f"⏳ Duration: {days}",
        f"📱 {ips}",
        f"💵 Price: {price_summary(plan)}",
    ]
    return "\n".join(line for line in lines if line is not None)
