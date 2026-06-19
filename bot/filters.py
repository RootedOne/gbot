from __future__ import annotations

from typing import Union

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings


class IsAdmin(BaseFilter):
    """Passes only for Telegram users listed in ADMIN_IDS (main bot) or node owner (node bots)."""

    async def __call__(self, event: Union[Message, CallbackQuery]) -> bool:
        user = event.from_user
        if user is None:
            return False
        
        bot = event.bot
        node_id = getattr(bot, "node_id", 0)
        if node_id == 0:
            return get_settings().is_admin(user.id)
        
        from bot.db import repo
        node = await repo.get_node(node_id)
        return bool(node and node.owner_tg_id == user.id)
