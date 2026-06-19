from __future__ import annotations

from typing import Callable, List

from aiogram.types import (
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from bot.db.models import Panel, PaymentMethod, Plan, Service

_METHOD_LABELS = {
    PaymentMethod.card: "💳 Card-to-card",
    PaymentMethod.stars: "⭐ Telegram Stars",
    PaymentMethod.crypto: "🪙 Crypto",
}


def language_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🇬🇧 English", callback_data="lang:en")
    builder.button(text="🇮🇷 فارسی", callback_data="lang:fa")
    builder.adjust(2)
    return builder.as_markup()


def main_menu_kb(is_admin: bool = False, is_reseller: bool = False, _ : Callable[[str], str] = lambda k: k) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=_("btn_buy_plan")),
        KeyboardButton(text=_("btn_my_services")),
    )
    builder.row(
        KeyboardButton(text=_("btn_balance")),
        KeyboardButton(text=_("btn_support")),
    )
    builder.row(
        KeyboardButton(text=_("btn_free_trial")),
        KeyboardButton(text=_("btn_help")),
    )
    
    bottom_buttons = []
    if is_reseller:
        bottom_buttons.append(KeyboardButton(text=_("btn_reseller_panel")))
    if is_admin:
        bottom_buttons.append(KeyboardButton(text=_("btn_admin_panel")))
        
    if bottom_buttons:
        builder.row(*bottom_buttons)
        
    return builder.as_markup(resize_keyboard=True)


def plans_kb(plans: List[Plan]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.button(text=f"{plan.title}", callback_data=f"plan:{plan.id}")
    builder.adjust(1)
    return builder.as_markup()


def plan_detail_kb(
    plan: Plan,
    methods: List[PaymentMethod],
    can_pay_with_balance: bool = False,
    _ : Callable[[str], str] = lambda k: k
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_pay_with_balance:
        builder.button(
            text=_("btn_pay_balance"), callback_data=f"buy:{plan.id}:wallet"
        )
    for method in methods:
        builder.button(
            text=_METHOD_LABELS.get(method, method.value),
            callback_data=f"buy:{plan.id}:{method.value}",
        )
    builder.button(
        text=_("btn_bulk_buy"), callback_data=f"plan_bulk:{plan.id}"
    )
    builder.button(text=_("btn_back_to_plans"), callback_data="plans:list")
    builder.adjust(1)
    return builder.as_markup()


def bulk_qty_kb(
    plan_id: int,
    _ : Callable[[str], str] = lambda k: k
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for qty in [2, 3, 5, 10]:
        builder.button(text=str(qty), callback_data=f"bulk_qty:{plan_id}:{qty}")
    builder.button(text=_("btn_custom"), callback_data=f"bulk_qty:{plan_id}:custom")
    builder.button(text=_("btn_back"), callback_data=f"plan:{plan_id}")
    builder.adjust(4, 1, 1)
    return builder.as_markup()


def bulk_payment_kb(
    plan_id: int,
    qty: int,
    methods: List[PaymentMethod],
    can_pay_with_balance: bool = False,
    _ : Callable[[str], str] = lambda k: k
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_pay_with_balance:
        builder.button(
            text=_("btn_pay_balance"), callback_data=f"bulk_buy:{plan_id}:{qty}:wallet"
        )
    for method in methods:
        builder.button(
            text=_METHOD_LABELS.get(method, method.value),
            callback_data=f"bulk_buy:{plan_id}:{qty}:{method.value}",
        )
    builder.button(text=_("btn_back"), callback_data=f"plan_bulk:{plan_id}")
    builder.adjust(1)
    return builder.as_markup()


def balance_menu_kb(can_charge: bool = True, _ : Callable[[str], str] = lambda k: k) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_charge:
        builder.button(text=_("btn_charge_balance"), callback_data="bal:charge")
    builder.button(text=_("btn_history"), callback_data="bal:history")
    builder.adjust(1)
    return builder.as_markup()


def services_kb(services: List[Service]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for svc in services:
        builder.button(text=f"🛡 {svc.email}", callback_data=f"svc:{svc.id}")
    builder.adjust(1)
    return builder.as_markup()


def delivery_kb(service_id: int, _ : Callable[[str], str] = lambda k: k) -> InlineKeyboardMarkup:
    """Shown after fulfillment — user can request individual config links."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_("btn_get_config_links"),
        callback_data=f"svc:sendlinks:{service_id}",
    )
    return builder.as_markup()


def service_actions_kb(
    service_id: int, *, can_migrate: bool = False, _ : Callable[[str], str] = lambda k: k
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=_("btn_usage"), callback_data=f"svc:usage:{service_id}")
    builder.button(text=_("btn_configs"), callback_data=f"svc:configs:{service_id}")
    builder.button(
        text=_("btn_get_config_links"),
        callback_data=f"svc:sendlinks:{service_id}",
    )
    if can_migrate:
        builder.button(text=_("btn_migrate"), callback_data=f"svc:migrate:{service_id}")
    builder.button(text=_("btn_buy_extra_gb"), callback_data=f"svc:buy_gb:{service_id}")
    builder.button(text=_("btn_buy_extra_time"), callback_data=f"svc:buy_time:{service_id}")
    builder.button(text=_("btn_renew"), callback_data=f"svc:renew:{service_id}")
    builder.button(text=_("btn_upgrade"), callback_data=f"svc:upgrade:{service_id}")
    builder.button(text=_("btn_get_new_link"), callback_data=f"svc:regen:{service_id}")
    builder.button(text=_("btn_delete_service"), callback_data=f"svc:delete:{service_id}")
    builder.adjust(2)
    return builder.as_markup()


def migration_targets_kb(service_id: int, panels: List[Panel], _ : Callable[[str], str] = lambda k: k) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for panel in panels:
        builder.button(
            text=f"🖥 {panel.name}",
            callback_data=f"svc:migrateto:{service_id}:{panel.id}",
        )
    builder.button(text=_("btn_back"), callback_data=f"svc:{service_id}")
    builder.adjust(1)
    return builder.as_markup()


def renew_plans_kb(service_id: int, plans: List[Plan], _ : Callable[[str], str] = lambda k: k) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.button(
            text=plan.title, callback_data=f"renewplan:{service_id}:{plan.id}"
        )
    builder.button(text=_("btn_back"), callback_data=f"svc:{service_id}")
    builder.adjust(1)
    return builder.as_markup()


def upgrade_plans_kb(service_id: int, plans: List[Plan], _ : Callable[[str], str] = lambda k: k) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.button(
            text=plan.title, callback_data=f"upgplan:{service_id}:{plan.id}"
        )
    builder.button(text=_("btn_back"), callback_data=f"svc:{service_id}")
    builder.adjust(1)
    return builder.as_markup()


def upgrade_payment_methods_kb(
    service_id: int,
    plan_id: int,
    methods: List[PaymentMethod],
    can_pay_with_balance: bool = False,
    _: Callable[[str], str] = lambda k: k
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_pay_with_balance:
        builder.button(
            text=_("btn_pay_balance"),
            callback_data=f"upgbuy:{service_id}:{plan_id}:wallet"
        )
    for method in methods:
        builder.button(
            text=_METHOD_LABELS.get(method, method.value),
            callback_data=f"upgbuy:{service_id}:{plan_id}:{method.value}"
        )
    builder.button(text=_("btn_back"), callback_data=f"svc:{service_id}")
    builder.adjust(1)
    return builder.as_markup()


def trial_panels_kb(panels: List[Panel], _: Callable[[str], str] = lambda k: k) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for panel in panels:
        builder.button(
            text=f"🚀 {panel.name}",
            callback_data=f"trial:{panel.id}",
        )
    builder.adjust(1)
    return builder.as_markup()


def extra_gb_packages_kb(service_id: int, plan: Optional[Plan], _: Callable[[str], str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if plan and plan.extra_gb_mode == "strict" and plan.extra_gb_packages:
        for pkg in plan.extra_gb_packages:
            gb = pkg.get("gb")
            builder.button(
                text=f"➕ {gb} GB",
                callback_data=f"svc:buy_gb_pkg:{service_id}:{gb}"
            )
    else:
        for gb in [10, 20, 50, 100]:
            builder.button(
                text=f"➕ {gb} GB",
                callback_data=f"svc:buy_gb_pkg:{service_id}:{gb}"
            )
        builder.button(
            text=_("btn_custom_amount"),
            callback_data=f"svc:buy_custom_gb:{service_id}"
        )
    builder.button(text=_("btn_back"), callback_data=f"svc:{service_id}")
    builder.adjust(2)
    return builder.as_markup()


def extra_time_packages_kb(service_id: int, plan: Optional[Plan], _: Callable[[str], str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if plan and plan.extra_time_mode == "strict" and plan.extra_time_packages:
        for pkg in plan.extra_time_packages:
            days = pkg.get("days")
            builder.button(
                text=f"⏱ {days} {_('days_label')}",
                callback_data=f"svc:buy_time_pkg:{service_id}:{days}"
            )
    else:
        for days in [7, 15, 30, 90]:
            builder.button(
                text=f"⏱ {days} {_('days_label')}",
                callback_data=f"svc:buy_time_pkg:{service_id}:{days}"
            )
        builder.button(
            text=_("btn_custom_amount"),
            callback_data=f"svc:buy_custom_time:{service_id}"
        )
    builder.button(text=_("btn_back"), callback_data=f"svc:{service_id}")
    builder.adjust(2)
    return builder.as_markup()


def addon_payment_methods_kb(
    service_id: int,
    kind: str,
    amount: float,
    methods: List[PaymentMethod],
    can_pay_with_balance: bool = False,
    _: Callable[[str], str] = lambda k: k
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_pay_with_balance:
        builder.button(
            text=_("btn_pay_balance"),
            callback_data=f"abuy:{service_id}:{kind}:{amount}:wallet"
        )
    for method in methods:
        builder.button(
            text=_METHOD_LABELS.get(method, method.value),
            callback_data=f"abuy:{service_id}:{kind}:{amount}:{method.value}"
        )
    builder.button(text=_("btn_back"), callback_data=f"svc:{service_id}")
    builder.adjust(1)
    return builder.as_markup()
