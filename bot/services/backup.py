from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Optional

from aiogram import Bot
from aiogram.types import FSInputFile

from bot.config import get_settings
from bot.db import repo

logger = logging.getLogger(__name__)


def get_db_file_path() -> str:
    """Parse DATABASE_URL settings to locate the local SQLite file path."""
    settings = get_settings()
    url = settings.database_url
    # e.g., sqlite+aiosqlite:///./vpnbot.db or sqlite:///./vpnbot.db
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            return url[len(prefix):]
    return "./vpnbot.db"


def parse_duration_to_seconds(duration_str: str) -> int:
    """Parse duration string like '30m', '12h', '1d', '2w' to seconds.

    Returns 0 if duration_str is '0' (meaning disabled).
    Raises ValueError if format is invalid.
    """
    s = duration_str.strip().lower()
    if s == "0" or not s:
        return 0

    match = re.match(r"^(\d+)([mhwd])$", s)
    if not match:
        raise ValueError("Invalid duration format. Use e.g. 30m, 12h, 1d, 2w")

    value = int(match.group(1))
    unit = match.group(2)

    if unit == "m":
        return value * 60
    elif unit == "h":
        return value * 3600
    elif unit == "d":
        return value * 86400
    elif unit == "w":
        return value * 604800
    else:
        raise ValueError("Unsupported unit")


async def perform_backup(bot: Bot, target_chat_id: Optional[int] = None) -> bool:
    """Perform database backup by sending the sqlite file to the designated admin.

    If target_chat_id is specified, sends to that ID (e.g. for manual triggers).
    Otherwise, sends to the first admin listed in ADMIN_IDS configuration.
    Returns True if backup succeeded, False otherwise.
    """
    settings = get_settings()
    chat_id = target_chat_id
    if chat_id is None:
        if not settings.admin_ids:
            logger.error("No ADMIN_IDS configured. Auto backup failed.")
            return False
        chat_id = settings.admin_ids[0]

    db_path = get_db_file_path()
    if not os.path.exists(db_path):
        logger.error("Database file not found at path: %s", db_path)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text="❌ <b>Database backup failed:</b> Database file not found."
            )
        except Exception:
            pass
        return False

    try:
        document = FSInputFile(db_path, filename="vpnbot.db")
        t_str = time.strftime("%Y-%m-%d %H:%M:%S")
        is_auto = "Auto" if target_chat_id is None else "Manual"
        await bot.send_document(
            chat_id=chat_id,
            document=document,
            caption=f"💾 <b>{is_auto} Database Backup</b>\n📅 Time: <code>{t_str}</code>"
        )
        logger.info("%s database backup successfully sent to chat %s.", is_auto, chat_id)
        return True
    except Exception as e:
        logger.exception("Failed to send database backup to chat %s: %s", chat_id, e)
        return False


async def run_backup_scheduler(bot: Bot) -> None:
    """Background task running backup scheduler."""
    logger.info("Backup scheduler started.")
    while True:
        try:
            # Check every 60 seconds
            await asyncio.sleep(60)

            # Get backup interval setting from DB (default is 24h)
            interval_str = await repo.get_setting("backup_interval", "24h")
            try:
                interval_seconds = parse_duration_to_seconds(interval_str)
            except ValueError:
                # Fallback to 24h if invalid
                interval_seconds = 24 * 3600

            if interval_seconds <= 0:
                # Auto backup disabled
                continue

            last_backup_str = await repo.get_setting("last_backup_time", "0")
            try:
                last_backup_time = float(last_backup_str)
            except ValueError:
                last_backup_time = 0.0

            current_time = time.time()
            if current_time - last_backup_time >= interval_seconds:
                logger.info("Triggering automatic database backup...")
                success = await perform_backup(bot)
                if success:
                    # Update last backup time only on success
                    await repo.set_setting("last_backup_time", str(current_time))
                else:
                    # If failed, retry in 5 minutes by updating last backup time to current_time - interval_seconds + 300
                    retry_time = current_time - interval_seconds + 300
                    await repo.set_setting("last_backup_time", str(retry_time))

        except asyncio.CancelledError:
            logger.info("Backup scheduler cancelled.")
            break
        except Exception as e:
            logger.exception("Error in backup scheduler: %s", e)
