from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

from bot.config import get_settings, load_settings_from_db, _get_raw_settings
from bot.connectivity.xray import XrayConnection, XrayStartError
from bot.db.base import engine, init_db
from bot.handlers.admin import (
    broadcast as admin_broadcast,
    dashboard as admin_dashboard,
    orders_admin,
    panels_admin,
    plans_admin,
    users_admin,
    resellers_admin,
    income_admin,
)
from bot.handlers.user import balance, checkout, myservices, plans, start, freetrial, reseller_panel
from bot.middlewares import ActiveBotMiddleware, BannedMiddleware, I18nMiddleware, ThrottlingMiddleware
from bot.panel.client import close_panel
from bot.payments.base import setup_providers
from bot.web.ipn import build_web_app
from bot.services.backup import run_backup_scheduler


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("vpnbot")


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())

    # Set active_bot context first
    dp.message.outer_middleware(ActiveBotMiddleware())
    dp.callback_query.outer_middleware(ActiveBotMiddleware())

    # Throttling middleware (limits messages/callbacks)
    # We apply this first so we don't even process banned or i18n logic if spamming
    dp.message.outer_middleware(ThrottlingMiddleware(rate_limit=1.5))
    dp.callback_query.outer_middleware(ThrottlingMiddleware(rate_limit=1.5))

    # Block banned users before any handler runs.
    dp.message.outer_middleware(BannedMiddleware())
    dp.callback_query.outer_middleware(BannedMiddleware())

    # Add i18n middleware
    dp.message.outer_middleware(I18nMiddleware())
    dp.callback_query.outer_middleware(I18nMiddleware())

    # Admin routers first so admin-only buttons/states win over user routers.
    dp.include_router(admin_dashboard.router)
    dp.include_router(plans_admin.router)
    dp.include_router(panels_admin.router)
    dp.include_router(orders_admin.router)
    dp.include_router(users_admin.router)
    dp.include_router(admin_broadcast.router)
    dp.include_router(resellers_admin.router)
    dp.include_router(income_admin.router)

    # User routers
    dp.include_router(start.router)
    dp.include_router(balance.router)
    dp.include_router(plans.router)
    dp.include_router(checkout.router)
    dp.include_router(freetrial.router)
    dp.include_router(myservices.router)
    dp.include_router(reseller_panel.router)

    return dp


async def _build_session(settings):
    """Return (session, xray_connection) based on the configured connection mode.

    DIRECT -> (None, None)
    PROXY  -> (AiohttpSession via PROXY_URL, None)
    XRAY   -> launch xray-core from XRAY_CONFIG_URL, route via its local SOCKS port
    """
    mode = settings.connection_mode
    if mode == "PROXY":
        if not settings.telegram_proxy:
            logger.warning("PROXY mode but PROXY_URL is empty; using DIRECT.")
            return None, None
        logger.info("Telegram connection: PROXY via %s", settings.telegram_proxy)
        return AiohttpSession(proxy=settings.telegram_proxy), None

    if mode == "XRAY":
        if not settings.xray_config_url:
            logger.warning("XRAY mode but XRAY_CONFIG_URL is empty; using DIRECT.")
            return None, None
        xray = XrayConnection(
            share_link=settings.xray_config_url,
            socks_port=settings.xray_socks_port,
            xray_bin=settings.xray_bin,
        )
        try:
            proxy_url = await xray.start()
        except XrayStartError as exc:
            logger.error("XRAY mode failed (%s); falling back to DIRECT.", exc)
            await xray.stop()
            return None, None
        logger.info("Telegram connection: XRAY via %s", proxy_url)
        return AiohttpSession(proxy=proxy_url), xray

    logger.info("Telegram connection: DIRECT")
    return None, None


async def _start_webhook_server(bot: Bot) -> web.AppRunner | None:
    settings = get_settings()
    app = build_web_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.webhook_host, settings.webhook_port)
    await site.start()
    logger.info(
        "Web/IPN server listening on %s:%s",
        settings.webhook_host,
        settings.webhook_port,
    )
    return runner



async def main() -> None:
    settings = get_settings()
    setup_providers()
    await init_db()
    # Apply raw settings load
    await load_settings_from_db(_get_raw_settings())

    session, xray = await _build_session(settings)

    bot = Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    bot.node_id = 0
    dp = build_dispatcher()

    from bot.services.nodes.manager import start_all_nodes, stop_all_nodes
    await start_all_nodes(dp)

    runner = await _start_webhook_server(bot)

    backup_task = asyncio.create_task(run_backup_scheduler(bot))

    logger.info("Starting bot polling for %s …", settings.brand_name)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        logger.info("Shutting down…")
        await stop_all_nodes()
        backup_task.cancel()
        try:
            await backup_task
        except asyncio.CancelledError:
            pass
        if runner is not None:
            await runner.cleanup()
        if xray is not None:
            await xray.stop()
        await close_panel()
        await bot.session.close()
        await engine.dispose()



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Stopped.")
