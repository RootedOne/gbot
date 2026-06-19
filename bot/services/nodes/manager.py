from __future__ import annotations

import asyncio
import logging
from typing import Dict

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import update_node_cache, remove_node_cache
from bot.db import repo

logger = logging.getLogger("vpnbot.node_manager")

# Dictionary to hold the polling tasks: node_id -> (asyncio.Task, Bot)
_NODE_BOT_TASKS: Dict[int, tuple[asyncio.Task, Bot]] = {}


async def start_node_bot(bot_token: str, node_id: int, dp: Dispatcher) -> bool:
    """Start polling for a reseller Node Bot and cache its settings."""
    if node_id in _NODE_BOT_TASKS:
        logger.warning("Node bot #%d is already running.", node_id)
        return True

    # 1. Fetch node settings and update settings cache
    node = await repo.get_node(node_id)
    if not node:
        logger.error("Node bot #%d not found in database.", node_id)
        return False

    update_node_cache(
        node_id=node.id,
        owner_tg_id=node.owner_tg_id,
        brand_name=node.brand_name,
        support_contact=node.support_contact,
        card_number=node.card_number,
        card_holder=node.card_holder,
    )

    # 2. Initialize the Bot instance
    try:
        bot = Bot(
            token=bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        # Verify bot token by fetching bot info
        bot_info = await bot.get_me()
        await repo.update_node(node_id, bot_username=f"@{bot_info.username}")
        
        bot.node_id = node_id
        bot.is_node = True

        # 3. Spawn the polling loop in a background task
        # We bypass start_polling's self._running_lock to support running multiple bot pollers concurrently.
        task = asyncio.create_task(
            dp._polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
                dispatcher=dp,
            )
        )
        _NODE_BOT_TASKS[node_id] = (task, bot)
        logger.info("Started Node Bot %s (#%d) for reseller %d", bot_info.username, node_id, node.owner_tg_id)
        return True
    except Exception as exc:
        logger.exception("Failed to start Node Bot #%d: %s", node_id, exc)
        remove_node_cache(node_id)
        return False


async def stop_node_bot(node_id: int) -> bool:
    """Stop polling for a reseller Node Bot and clean up."""
    if node_id not in _NODE_BOT_TASKS:
        return False

    task, bot = _NODE_BOT_TASKS.pop(node_id)
    logger.info("Stopping Node Bot #%d...", node_id)
    
    # Cancel polling task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning("Exception during Node Bot #%d task cleanup: %s", node_id, exc)

    # Close session
    try:
        await bot.session.close()
    except Exception:
        pass

    remove_node_cache(node_id)
    logger.info("Node Bot #%d stopped successfully.", node_id)
    return True


async def start_all_nodes(dp: Dispatcher) -> None:
    """Load all active reseller Node Bots from DB and start polling."""
    nodes = await repo.list_all_nodes()
    logger.info("Initializing connected reseller bots. Found %d total node(s).", len(nodes))
    for node in nodes:
        if node.is_active:
            success = await start_node_bot(node.bot_token, node.id, dp)
            if not success:
                logger.error("Failed to auto-start Node Bot #%d on boot.", node.id)


async def stop_all_nodes() -> None:
    """Cancel all running reseller Node Bots polling tasks."""
    node_ids = list(_NODE_BOT_TASKS.keys())
    for node_id in node_ids:
        await stop_node_bot(node_id)
