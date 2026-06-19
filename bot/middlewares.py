from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from bot.config import get_settings
from bot.db import repo

logger = logging.getLogger(__name__)


class BannedMiddleware(BaseMiddleware):
    """Drops updates from banned (is_blocked) users. Admins are never blocked."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        bot = data.get("bot")
        node_id = getattr(bot, "node_id", 0)

        # Check if user is admin on the current bot
        is_admin = False
        if node_id == 0:
            is_admin = get_settings().is_admin(user.id) if user else False
        else:
            if user:
                node = await repo.get_node(node_id)
                is_admin = bool(node and node.owner_tg_id == user.id)

        if user is not None and not is_admin:
            if await repo.is_user_blocked(user.id, node_id):
                if isinstance(event, CallbackQuery):
                    await event.answer(
                        "🚫 You are banned from this bot.", show_alert=True
                    )
                elif isinstance(event, Message):
                    try:
                        await event.answer("🚫 You are banned from using this bot.")
                    except Exception:  # noqa: BLE001
                        pass
                return None
        return await handler(event, data)


from functools import partial
from bot.utils.locales import get_text

class I18nMiddleware(BaseMiddleware):
    """Provides translation function `_` and user language to handlers."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        bot = data.get("bot")
        node_id = getattr(bot, "node_id", 0)
        lang = "en"
        if user is not None:
            db_user = await repo.get_user(user.id, node_id)
            if db_user:
                lang = db_user.lang

        data["lang"] = lang
        data["_"] = partial(get_text, lang)
        return await handler(event, data)


from cachetools import TTLCache

class ThrottlingMiddleware(BaseMiddleware):
    """Simple rate limit to prevent spamming."""

    def __init__(self, rate_limit: float = 1.5):
        self.cache = TTLCache(maxsize=10_000, ttl=rate_limit)
        # Separate cache to prevent spamming the warning message itself
        self.warning_cache = TTLCache(maxsize=10_000, ttl=5.0)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        bot = data.get("bot")
        node_id = getattr(bot, "node_id", 0)

        # Check if user is admin on the current bot
        is_admin = False
        if node_id == 0:
            is_admin = get_settings().is_admin(user.id) if user else False
        else:
            if user:
                node = await repo.get_node(node_id)
                is_admin = bool(node and node.owner_tg_id == user.id)

        if user is not None:
            # We skip admins from rate limiting
            if not is_admin:
                if user.id in self.cache:
                    if isinstance(event, CallbackQuery):
                        # Show a prominent popup they must click OK to close
                        await event.answer("⚠️ Please slow down! You are clicking too fast.", show_alert=True)
                    elif isinstance(event, Message):
                        # Warn via text, but only once every 5 seconds to prevent the bot from getting blocked
                        if user.id not in self.warning_cache:
                            self.warning_cache[user.id] = True
                            try:
                                await event.answer("⚠️ Please slow down! Too many requests.")
                            except Exception:
                                pass
                    return None
                self.cache[user.id] = True

        return await handler(event, data)


class ActiveBotMiddleware(BaseMiddleware):
    """Sets the active bot in a ContextVar for the current request context."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        from bot.config import active_bot
        bot = data.get("bot")
        token = active_bot.set(bot)
        try:
            return await handler(event, data)
        finally:
            active_bot.reset(token)


