from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pydantic import Field


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Telegram
    bot_token: str
    # Stored as raw string ("1,2,3") to avoid pydantic-settings JSON pre-parsing.
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")

    # How the bot reaches the Telegram API: DIRECT | PROXY | XRAY
    # (left blank -> derived from legacy PROXY_MODE for backward compatibility)
    connect_mode: str = ""

    # PROXY mode: reach Telegram through an http/https/socks5 proxy.
    # PROXY_MODE accepts ON/OFF (also true/false/1/0); kept for backward compat.
    proxy_mode: str = "OFF"
    proxy_url: str = ""

    # XRAY mode: spin up a local xray-core from a share link (vless/vmess/trojan/ss)
    # and route Telegram through the SOCKS proxy it exposes.
    xray_config_url: str = ""
    xray_bin: str = "xray"
    xray_socks_port: int = 10808

    # Database
    database_url: str = "sqlite+aiosqlite:///./vpnbot.db"

    # UX
    default_lang: str = "en"
    support_contact: str = "@support"
    brand_name: str = "My VPN"

    # Card-to-card
    card_number: str = ""
    card_holder: str = ""
    fiat_currency: str = "IRR"

    # Telegram Stars
    stars_enabled: bool = True

    # Crypto (NowPayments)
    crypto_enabled: bool = False
    nowpayments_api_key: str = ""
    nowpayments_ipn_secret: str = ""
    public_base_url: str = ""
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8080

    # Extra GB/Time Pricing Settings
    extra_gb_price_fiat: float = 5000.0
    extra_gb_price_stars: int = 1
    extra_gb_price_usd: float = 0.05
    extra_time_price_fiat: float = 2000.0
    extra_time_price_stars: int = 1
    extra_time_price_usd: float = 0.02

    @field_validator("public_base_url", mode="before")
    @classmethod
    def _strip_trailing_slash(cls, value: object) -> object:
        if isinstance(value, str):
            return value.rstrip("/")
        return value

    @property
    def admin_ids(self) -> List[int]:
        out: List[int] = []
        for part in (self.admin_ids_raw or "").replace(" ", "").split(","):
            if part:
                try:
                    out.append(int(part))
                except ValueError:
                    continue
        return out

    @property
    def proxy_enabled(self) -> bool:
        return (
            self.proxy_mode.strip().upper() in ("ON", "TRUE", "1", "YES")
            and bool(self.proxy_url.strip())
        )

    @property
    def telegram_proxy(self) -> str:
        return self.proxy_url.strip()

    @property
    def connection_mode(self) -> str:
        """Effective Telegram connection mode: DIRECT | PROXY | XRAY."""
        mode = self.connect_mode.strip().upper()
        if mode in ("DIRECT", "PROXY", "XRAY"):
            return mode
        # Backward compatibility: derive from legacy PROXY_MODE.
        if self.proxy_enabled:
            return "PROXY"
        return "DIRECT"

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


import contextvars
from typing import Any, Dict, Optional
from aiogram import Bot

active_bot: contextvars.ContextVar[Optional[Bot]] = contextvars.ContextVar("active_bot", default=None)
_NODE_SETTINGS_CACHE: Dict[int, Dict[str, Any]] = {}

def update_node_cache(
    node_id: int,
    owner_tg_id: int,
    brand_name: str,
    support_contact: str,
    card_number: str,
    card_holder: str,
) -> None:
    _NODE_SETTINGS_CACHE[node_id] = {
        "owner_tg_id": owner_tg_id,
        "brand_name": brand_name,
        "support_contact": support_contact,
        "card_number": card_number,
        "card_holder": card_holder,
    }

def remove_node_cache(node_id: int) -> None:
    _NODE_SETTINGS_CACHE.pop(node_id, None)

def get_node_cached_val(node_id: int, key: str, default: Any) -> Any:
    return _NODE_SETTINGS_CACHE.get(node_id, {}).get(key, default)


class NodeAwareSettingsWrapper:
    def __init__(self, raw_settings: Settings):
        self._settings = raw_settings

    def __getattr__(self, name: str) -> Any:
        return getattr(self._settings, name)

    @property
    def brand_name(self) -> str:
        bot = active_bot.get()
        node_id = getattr(bot, "node_id", 0) if bot else 0
        if node_id > 0:
            return get_node_cached_val(node_id, "brand_name", self._settings.brand_name)
        return self._settings.brand_name

    @property
    def support_contact(self) -> str:
        bot = active_bot.get()
        node_id = getattr(bot, "node_id", 0) if bot else 0
        if node_id > 0:
            return get_node_cached_val(node_id, "support_contact", self._settings.support_contact)
        return self._settings.support_contact

    @property
    def card_number(self) -> str:
        bot = active_bot.get()
        node_id = getattr(bot, "node_id", 0) if bot else 0
        if node_id > 0:
            return get_node_cached_val(node_id, "card_number", self._settings.card_number)
        return self._settings.card_number

    @property
    def card_holder(self) -> str:
        bot = active_bot.get()
        node_id = getattr(bot, "node_id", 0) if bot else 0
        if node_id > 0:
            return get_node_cached_val(node_id, "card_holder", self._settings.card_holder)
        return self._settings.card_holder

    @property
    def admin_ids(self) -> List[int]:
        bot = active_bot.get()
        node_id = getattr(bot, "node_id", 0) if bot else 0
        if node_id > 0:
            owner_id = get_node_cached_val(node_id, "owner_tg_id", None)
            return [owner_id] if owner_id else []
        return self._settings.admin_ids

    def is_admin(self, user_id: int) -> bool:
        bot = active_bot.get()
        node_id = getattr(bot, "node_id", 0) if bot else 0
        if node_id > 0:
            owner_id = get_node_cached_val(node_id, "owner_tg_id", None)
            return user_id == owner_id
        return self._settings.is_admin(user_id)


@lru_cache(maxsize=1)
def _get_raw_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def get_settings() -> Any:
    return NodeAwareSettingsWrapper(_get_raw_settings())


async def load_settings_from_db(settings: Settings) -> None:
    from bot.db import repo

    keys = [
        "brand_name",
        "support_contact",
        "fiat_currency",
        "card_number",
        "card_holder",
        "stars_enabled",
        "crypto_enabled",
        "admin_ids_raw",
        "nowpayments_api_key",
        "nowpayments_ipn_secret",
        "public_base_url",
        "extra_gb_price_fiat",
        "extra_gb_price_stars",
        "extra_gb_price_usd",
        "extra_time_price_fiat",
        "extra_time_price_stars",
        "extra_time_price_usd",
    ]

    for key in keys:
        val = await repo.get_setting(key)
        if val is not None:
            if key in ("stars_enabled", "crypto_enabled"):
                setattr(settings, key, val.lower() in ("true", "1", "yes", "on"))
            elif key in ("extra_gb_price_stars", "extra_time_price_stars"):
                setattr(settings, key, int(val))
            elif key in ("extra_gb_price_fiat", "extra_gb_price_usd", "extra_time_price_fiat", "extra_time_price_usd"):
                setattr(settings, key, float(val))
            else:
                setattr(settings, key, val)

