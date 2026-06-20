from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class CheckoutStates(StatesGroup):
    awaiting_receipt = State()
    entering_extra_gb = State()
    entering_extra_days = State()
    awaiting_bulk_qty = State()
    awaiting_promo_code = State()


class PlanForm(StatesGroup):
    title = State()
    description = State()
    traffic_gb = State()
    duration_days = State()
    limit_ip = State()
    panel = State()
    inbounds = State()
    price_fiat = State()
    price_stars = State()
    price_usd = State()


class EditPlanForm(StatesGroup):
    value = State()
    inbounds = State()
    panel_confirm = State()
    addon_value = State()
    addon_pkgs = State()


class PanelForm(StatesGroup):
    name = State()
    base_url = State()
    token = State()
    verify_tls = State()
    sub_base_url = State()
    use_middle_server = State()
    middle_server_url = State()
    middle_server_token = State()


class EditPanelForm(StatesGroup):
    value = State()
    migration_inbounds = State()
    trial_inbounds = State()
    reseller_inbounds = State()


class ResellerInboundForm(StatesGroup):
    inbound_ids = State()


class BalanceForm(StatesGroup):
    amount = State()


class AdminUserForm(StatesGroup):
    create_target = State()
    create_plan = State()
    find_service = State()
    extend_days = State()
    balance_target = State()
    balance_amount = State()
    history_target = State()
    whisper_text = State()


class BroadcastForm(StatesGroup):
    message = State()


class BackupSettingsForm(StatesGroup):
    interval = State()


class SettingsForm(StatesGroup):
    value = State()


class PromoCodeForm(StatesGroup):
    code = State()
    discount_type = State()
    discount_value = State()
    max_uses = State()
    expiry_days = State()


class EditPromoCodeForm(StatesGroup):
    value = State()

