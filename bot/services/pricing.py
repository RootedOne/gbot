from __future__ import annotations

from typing import List, Optional, Tuple

from bot.config import get_settings
from bot.db.models import PaymentMethod, Plan, PromoCode


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
    if method in (PaymentMethod.card, PaymentMethod.wallet):
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


async def adjust_plan_for_reseller(plan: Plan, user_tg_id: int, node_id: int = 0) -> None:
    """If the bot is the main bot (node_id == 0) and the user is a reseller,
    override the plan's prices to reflect the reseller wholesale pricing.
    """
    if node_id == 0:
        from bot.db import repo
        user = await repo.get_user(user_tg_id, 0)
        if user and user.is_reseller:
            if plan.traffic_gb == 0:
                reseller_price = await repo.get_reseller_unlimited_price(user_tg_id, plan.panel_id)
            else:
                gb_price = await repo.get_reseller_gb_price(user_tg_id, plan.panel_id)
                reseller_price = float(plan.traffic_gb * gb_price)
            
            plan.price_fiat = reseller_price
            plan.price_stars = 0
            plan.price_usd = 0.0


def adjust_plan_for_promo(plan: Plan, promo: PromoCode) -> float:
    """Mutate plan prices in memory based on the promo code. Returns the discount amount in fiat."""
    discount_amount = 0.0
    if promo.discount_type == "percentage":
        factor = 1.0 - (promo.discount_value / 100.0)
        discount_amount = float(plan.price_fiat) * (promo.discount_value / 100.0)
        plan.price_fiat = max(0.0, float(plan.price_fiat) * factor)
        plan.price_usd = max(0.0, float(plan.price_usd) * factor)
        plan.price_stars = max(0, int(plan.price_stars * factor))
    else:
        discount_amount = min(float(plan.price_fiat), promo.discount_value)
        plan.price_fiat = max(0.0, float(plan.price_fiat) - promo.discount_value)
    return discount_amount


