from __future__ import annotations

from typing import List, Sequence

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import Panel, Plan, PromoCode
from bot.panel.schemas import InboundOption


def admin_menu_kb(node_id: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Dashboard", callback_data="adm:dash")
    builder.button(text="🌐 Web UI Panel", callback_data="adm:webui")
    builder.button(text="💰 Income", callback_data="adm:income")
    builder.button(text="🗂 Plans", callback_data="adm:plans")
    if node_id == 0:
        builder.button(text="🖥 Panels", callback_data="adm:panels")
        builder.button(text="👥 Resellers", callback_data="adm:resellers")
    builder.button(text="🧾 Pending Receipts", callback_data="adm:orders")
    builder.button(text="👤 Manage Service", callback_data="adm:users")
    builder.button(text="📣 Broadcast", callback_data="adm:broadcast")
    builder.button(text="🎟 Promo Codes", callback_data="adm:promos")
    if node_id == 0:
        builder.button(text="⚙️ Settings", callback_data="adm:settings")
    else:
        builder.button(text="⚙️ Node Settings", callback_data="node:settings")
    builder.adjust(2)
    return builder.as_markup()



def admin_plans_kb(plans: List[Plan]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        flag = "🟢" if plan.is_active else "⚪️"
        builder.button(
            text=f"{flag} {plan.title}", callback_data=f"adm:plan:{plan.id}"
        )
    builder.button(text="➕ New Plan", callback_data="adm:plan:new")
    builder.button(text="⬅️ Back", callback_data="adm:menu")
    builder.adjust(1)
    return builder.as_markup()


def admin_plan_detail_kb(plan: Plan) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Edit", callback_data=f"adm:pedit:{plan.id}")
    toggle = "Disable" if plan.is_active else "Enable"
    builder.button(text=f"🔁 {toggle}", callback_data=f"adm:plan:toggle:{plan.id}")
    builder.button(text="🗑 Delete", callback_data=f"adm:plan:del:{plan.id}")
    builder.button(text="⬅️ Back", callback_data="adm:plans")
    builder.adjust(2)
    return builder.as_markup()


def admin_panels_kb(panels: List[Panel]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for panel in panels:
        flag = "🟢" if panel.is_active else "⚪️"
        builder.button(
            text=f"{flag} {panel.name}",
            callback_data=f"adm:panel:{panel.id}",
        )
    builder.button(text="➕ Add panel", callback_data="adm:panel:new")
    builder.button(text="⬅️ Back", callback_data="adm:menu")
    builder.adjust(1)
    return builder.as_markup()


def admin_panel_detail_kb(panel: Panel, *, orphan_count: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Edit", callback_data=f"adm:pnledit:{panel.id}")
    builder.button(text="🔌 Test", callback_data=f"adm:panel:test:{panel.id}")
    toggle = "Disable" if panel.is_active else "Enable"
    builder.button(text=f"🔁 {toggle}", callback_data=f"adm:panel:toggle:{panel.id}")
    if orphan_count > 0:
        builder.button(
            text=f"🔗 Backfill {orphan_count} service(s)",
            callback_data=f"adm:panel:backfill:{panel.id}",
        )
    builder.button(text="🗑 Delete", callback_data=f"adm:panel:del:{panel.id}")
    builder.button(text="⬅️ Back", callback_data="adm:panels")
    builder.adjust(2)
    return builder.as_markup()


def panel_edit_menu_kb(panel: Panel) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Name", callback_data=f"adm:pnlfield:{panel.id}:name")
    builder.button(text="🔗 Base URL", callback_data=f"adm:pnlfield:{panel.id}:base_url")
    builder.button(text="🔑 API token", callback_data=f"adm:pnlfield:{panel.id}:token")
    builder.button(text="🔒 TLS verify", callback_data=f"adm:pnlfield:{panel.id}:verify_tls")
    builder.button(text="📡 Sub base URL", callback_data=f"adm:pnlfield:{panel.id}:sub_base_url")
    migr = "✅" if panel.allow_migrations else "❌"
    builder.button(
        text=f"🔀 Allow migrations {migr}",
        callback_data=f"adm:panel:migratetoggle:{panel.id}",
    )
    builder.button(
        text="🔌 Migration inbounds",
        callback_data=f"adm:panel:minbounds:{panel.id}",
    )
    trial_flag = "✅" if panel.allow_trials else "❌"
    builder.button(
        text=f"🎁 Allow trials {trial_flag}",
        callback_data=f"adm:panel:trialtoggle:{panel.id}",
    )
    builder.button(
        text="🎁 Trial inbounds",
        callback_data=f"adm:panel:tinbounds:{panel.id}",
    )
    reseller_flag = "✅" if panel.allow_resellers else "❌"
    builder.button(
        text=f"💼 Allow resellers {reseller_flag}",
        callback_data=f"adm:panel:resellertoggle:{panel.id}",
    )
    builder.button(
        text="💼 Reseller inbounds",
        callback_data=f"adm:panel:resinbounds:{panel.id}",
    )
    builder.button(
        text="💰 Reseller Price",
        callback_data=f"adm:pnlfield:{panel.id}:reseller_gb_price",
    )
    builder.button(
        text="♾ Reseller Unlimited Price",
        callback_data=f"adm:pnlfield:{panel.id}:reseller_unlimited_price",
    )
    mid_flag = "✅" if getattr(panel, "use_middle_server", False) else "❌"
    builder.button(
        text=f"🔄 Middle Server {mid_flag}",
        callback_data=f"adm:panel:midservertoggle:{panel.id}",
    )
    if getattr(panel, "use_middle_server", False):
        builder.button(
            text="📡 Middle Server URL",
            callback_data=f"adm:pnlfield:{panel.id}:middle_server_url",
        )
        builder.button(
            text="🔑 Middle Server Token",
            callback_data=f"adm:pnlfield:{panel.id}:middle_server_token",
        )
    builder.button(text="⬅️ Back", callback_data=f"adm:panel:{panel.id}")
    builder.adjust(2)
    return builder.as_markup()


def panel_picker_kb(panels: List[Panel], prefix: str = "adm:planpanel") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for panel in panels:
        builder.button(
            text=f"🖥 {panel.name}",
            callback_data=f"{prefix}:{panel.id}",
        )
    builder.adjust(1)
    return builder.as_markup()


def plan_edit_menu_kb(plan: Plan) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🖥 Panel", callback_data=f"adm:pfield:{plan.id}:panel")
    builder.button(text="📝 Title", callback_data=f"adm:pfield:{plan.id}:title")
    builder.button(text="🧾 Description", callback_data=f"adm:pfield:{plan.id}:desc")
    builder.button(text="📦 Traffic GB", callback_data=f"adm:pfield:{plan.id}:traffic")
    builder.button(text="⏳ Duration", callback_data=f"adm:pfield:{plan.id}:duration")
    builder.button(text="📱 Device limit", callback_data=f"adm:pfield:{plan.id}:limitip")
    builder.button(text="🔌 Inbounds", callback_data=f"adm:pfield:{plan.id}:inbounds")
    builder.button(text="💳 Card price", callback_data=f"adm:pfield:{plan.id}:price_fiat")
    builder.button(text="⭐ Stars price", callback_data=f"adm:pfield:{plan.id}:price_stars")
    builder.button(text="🪙 USD price", callback_data=f"adm:pfield:{plan.id}:price_usd")
    builder.button(text="📦 Extra GB Price", callback_data=f"adm:pextra:{plan.id}:gb")
    builder.button(text="⏳ Extra Time Price", callback_data=f"adm:pextra:{plan.id}:time")
    builder.button(text="⬅️ Back", callback_data=f"adm:plan:{plan.id}")
    builder.adjust(2)
    return builder.as_markup()


def inbound_picker_kb(
    options: Sequence[InboundOption],
    selected: Sequence[int],
    *,
    prefix: str = "adm:inb",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    selected_set = set(selected)
    for opt in options:
        mark = "✅" if opt.id in selected_set else "▫️"
        builder.button(
            text=f"{mark} {opt.remark} ({opt.protocol}:{opt.port})",
            callback_data=f"{prefix}:{opt.id}",
        )
    builder.button(text="✔️ Done", callback_data=f"{prefix}:done")
    builder.adjust(1)
    return builder.as_markup()


def confirm_kb(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Yes", callback_data=yes_cb)
    builder.button(text="❌ No", callback_data=no_cb)
    builder.adjust(2)
    return builder.as_markup()


def admin_settings_kb(stars_enabled: bool, crypto_enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Brand Name", callback_data="adm:set_cfg:brand_name")
    builder.button(text="💬 Support Contact", callback_data="adm:set_cfg:support_contact")
    builder.button(text="💵 Fiat Currency", callback_data="adm:set_cfg:fiat_currency")
    builder.button(text="💳 Card Number", callback_data="adm:set_cfg:card_number")
    builder.button(text="👤 Card Holder", callback_data="adm:set_cfg:card_holder")

    stars_icon = "🟢" if stars_enabled else "🔴"
    builder.button(text=f"⭐️ Stars: {stars_icon}", callback_data="adm:set_cfg_toggle:stars_enabled")

    crypto_icon = "🟢" if crypto_enabled else "🔴"
    builder.button(text=f"🪙 Crypto: {crypto_icon}", callback_data="adm:set_cfg_toggle:crypto_enabled")

    builder.button(text="🔑 NowPayments Key", callback_data="adm:set_cfg:nowpayments_api_key")
    builder.button(text="🔒 NowPayments Secret", callback_data="adm:set_cfg_toggle:nowpayments_ipn_secret")
    builder.button(text="🌐 Public Base URL", callback_data="adm:set_cfg:public_base_url")

    # Extra GB/Time prices
    builder.button(text="📦 Price per GB (Fiat)", callback_data="adm:set_cfg:extra_gb_price_fiat")
    builder.button(text="⭐️ Stars per GB", callback_data="adm:set_cfg:extra_gb_price_stars")
    builder.button(text="🪙 USD per GB", callback_data="adm:set_cfg:extra_gb_price_usd")
    builder.button(text="⏳ Price per Day (Fiat)", callback_data="adm:set_cfg:extra_time_price_fiat")
    builder.button(text="⭐️ Stars per Day", callback_data="adm:set_cfg:extra_time_price_stars")
    builder.button(text="🪙 USD per Day", callback_data="adm:set_cfg:extra_time_price_usd")

    builder.button(text="👥 Admins (IDs)", callback_data="adm:set_cfg:admin_ids_raw")
    builder.button(text="💾 Backup Interval", callback_data="adm:set_backup_interval")
    builder.button(text="📤 Backup Now", callback_data="adm:backup_now")
    builder.button(text="⬅️ Back", callback_data="adm:menu")
    builder.adjust(2)
    return builder.as_markup()


def plan_addon_pricing_kb(plan: Plan, addon_type: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    mode = getattr(plan, f"extra_{addon_type}_mode", "flexible")
    mode_emoji = "🔒 Strict" if mode == "strict" else "✍️ Flexible"
    
    builder.button(text=f"Mode: {mode_emoji}", callback_data=f"adm:pextra:mode:{plan.id}:{addon_type}")
    
    if mode == "flexible":
        builder.button(text="💳 Price Fiat", callback_data=f"adm:pextra:unit:{plan.id}:{addon_type}:fiat")
        builder.button(text="⭐ Price Stars", callback_data=f"adm:pextra:unit:{plan.id}:{addon_type}:stars")
        builder.button(text="🪙 Price USD", callback_data=f"adm:pextra:unit:{plan.id}:{addon_type}:usd")
    else:
        builder.button(text="📦 Configure Packages", callback_data=f"adm:pextra:pkgs:{plan.id}:{addon_type}")
        
    builder.button(text="⬅️ Back", callback_data=f"adm:pedit:{plan.id}")
    builder.adjust(1)
    return builder.as_markup()


def admin_promos_kb(promos: List[PromoCode]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for promo in promos:
        flag = "🟢" if promo.is_active else "⚪️"
        type_str = "%" if promo.discount_type == "percentage" else ""
        builder.button(
            text=f"{flag} {promo.code} ({int(promo.discount_value)}{type_str})",
            callback_data=f"adm:promo:{promo.id}"
        )
    builder.button(text="➕ New Promo", callback_data="adm:promo:new")
    builder.button(text="⬅️ Back", callback_data="adm:menu")
    builder.adjust(1)
    return builder.as_markup()


def admin_promo_detail_kb(promo: PromoCode) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Edit Code", callback_data=f"adm:prmedit:{promo.id}:code")
    builder.button(text="💰 Edit Value", callback_data=f"adm:prmedit:{promo.id}:value")
    builder.button(text="🔄 Toggle Type", callback_data=f"adm:promo:type:{promo.id}")
    toggle = "Disable" if promo.is_active else "Enable"
    builder.button(text=f"🔁 {toggle}", callback_data=f"adm:promo:toggle:{promo.id}")
    builder.button(text="🗑 Delete", callback_data=f"adm:promo:del:{promo.id}")
    builder.button(text="⬅️ Back", callback_data="adm:promos")
    builder.adjust(2)
    return builder.as_markup()

