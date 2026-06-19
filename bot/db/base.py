from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from bot.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False, future=True)
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)


# Columns added after the initial release. For existing SQLite databases we
# ALTER TABLE ADD COLUMN them since create_all() never alters existing tables.
_SQLITE_COLUMN_MIGRATIONS = {
    "users": [
        ("balance", "FLOAT NOT NULL DEFAULT 0"),
        ("node_id", "INTEGER NOT NULL DEFAULT 0"),
        ("is_reseller", "BOOLEAN NOT NULL DEFAULT 0"),
        ("reseller_gb_price", "FLOAT NOT NULL DEFAULT 0"),
        ("reseller_day_price", "FLOAT NOT NULL DEFAULT 0"),
        ("reseller_unlimited_price", "FLOAT NOT NULL DEFAULT 0")
    ],
    "orders": [
        ("kind", "VARCHAR(16) NOT NULL DEFAULT 'plan'"),
        ("node_id", "INTEGER NOT NULL DEFAULT 0"),
        ("extra_gb", "FLOAT"),
        ("extra_days", "INTEGER"),
        ("quantity", "INTEGER NOT NULL DEFAULT 1")
    ],
    "plans": [
        ("panel_id", "INTEGER"),
        ("node_id", "INTEGER NOT NULL DEFAULT 0"),
        ("extra_gb_price_fiat", "FLOAT"),
        ("extra_gb_price_stars", "INTEGER"),
        ("extra_gb_price_usd", "FLOAT"),
        ("extra_time_price_fiat", "FLOAT"),
        ("extra_time_price_stars", "INTEGER"),
        ("extra_time_price_usd", "FLOAT"),
        ("extra_gb_mode", "VARCHAR(16) NOT NULL DEFAULT 'flexible'"),
        ("extra_time_mode", "VARCHAR(16) NOT NULL DEFAULT 'flexible'"),
        ("extra_gb_packages", "JSON"),
        ("extra_time_packages", "JSON")
    ],
    "services": [
        ("panel_id", "INTEGER"),
        ("node_id", "INTEGER NOT NULL DEFAULT 0")
    ],
    "transactions": [
        ("node_id", "INTEGER NOT NULL DEFAULT 0")
    ],
    "panels": [
        ("allow_migrations", "BOOLEAN NOT NULL DEFAULT 0"),
        ("allow_trials", "BOOLEAN NOT NULL DEFAULT 0"),
        ("allow_resellers", "BOOLEAN NOT NULL DEFAULT 0"),
        ("migration_inbound_ids", "JSON"),
        ("trial_inbound_ids", "JSON"),
        ("reseller_inbound_ids", "JSON"),
        ("reseller_gb_price", "FLOAT NOT NULL DEFAULT 0"),
        ("reseller_unlimited_price", "FLOAT NOT NULL DEFAULT 0"),
        ("use_middle_server", "BOOLEAN NOT NULL DEFAULT 0"),
        ("middle_server_url", "VARCHAR(512) NOT NULL DEFAULT ''"),
        ("middle_server_token", "VARCHAR(512) NOT NULL DEFAULT ''")
    ],
    "reseller_panel_inbounds": [
        ("reseller_gb_price", "FLOAT"),
        ("reseller_unlimited_price", "FLOAT")
    ],
}


async def _migrate_sqlite_columns() -> None:
    if not engine.url.get_backend_name().startswith("sqlite"):
        return
    async with engine.begin() as conn:
        for table, columns in _SQLITE_COLUMN_MIGRATIONS.items():
            existing = await conn.run_sync(
                lambda sync_conn, t=table: [
                    row[1]
                    for row in sync_conn.exec_driver_sql(
                        f"PRAGMA table_info({t})"
                    ).fetchall()
                ]
            )
            if not existing:
                continue  # table doesn't exist yet; create_all will build it
            for name, ddl in columns:
                if name not in existing:
                    logger.info("Migrating: adding %s.%s", table, name)
                    await conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                    )


async def init_db() -> None:
    """Create all tables and apply lightweight column migrations."""
    # Import models so they are registered on the metadata before create_all.
    from bot.db import models  # noqa: F401

    await _migrate_sqlite_columns()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
