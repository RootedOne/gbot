from __future__ import annotations

import abc

from aiogram import Bot
from aiogram.fsm.context import FSMContext

from bot.db.models import Order, PaymentMethod, Plan


class PaymentProvider(abc.ABC):
    """Strategy for collecting payment for an order.

    All providers ultimately converge on `fulfillment.fulfill_order`, which
    provisions the service and delivers configs once payment is confirmed.
    """

    method: PaymentMethod

    @abc.abstractmethod
    async def start_checkout(
        self,
        bot: Bot,
        chat_id: int,
        order: Order,
        plan: Plan,
        state: FSMContext,
    ) -> None:
        """Send the user whatever they need to complete payment."""
        raise NotImplementedError


_registry: dict[PaymentMethod, PaymentProvider] = {}


def register(provider: PaymentProvider) -> None:
    _registry[provider.method] = provider


def get_provider(method: PaymentMethod) -> PaymentProvider:
    provider = _registry.get(method)
    if provider is None:
        raise KeyError(f"No payment provider registered for {method}")
    return provider


def setup_providers() -> None:
    """Instantiate and register all providers. Call once on startup."""
    from bot.payments.manual import ManualReceiptProvider
    from bot.payments.stars import StarsProvider
    from bot.payments.crypto import CryptoProvider

    register(ManualReceiptProvider())
    register(StarsProvider())
    register(CryptoProvider())
