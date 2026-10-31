import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from config import settings

logger = logging.getLogger("smart_parental_tracker.database")

is_sqlite = "sqlite" in settings.RESOLVED_DATABASE_URL
engine_kwargs = {
    "echo": False,
    "future": True,
}
if is_sqlite:
    engine_kwargs["connect_args"] = {
        "check_same_thread": False,
        "timeout": 30.0,
    }
else:
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_recycle"] = 300
    engine_kwargs["pool_size"] = 20
    engine_kwargs["max_overflow"] = 30
    if "postgres" in settings.RESOLVED_DATABASE_URL:
        engine_kwargs["connect_args"] = {"ssl": "require"}

engine = create_async_engine(
    settings.RESOLVED_DATABASE_URL,
    **engine_kwargs
)

from sqlalchemy import event

if is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-64000")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute("PRAGMA mmap_size=268435456")
            cursor.close()
        except Exception:
            pass

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)
async_session_maker = AsyncSessionLocal


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    """Initialize database tables schema and auto-seed if migrating."""
    async with engine.begin() as conn:
        # Import models so Base metadata has all tables registered
        import db.models  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)

        # Safe automatic migration for new Task columns
        from sqlalchemy import text
        new_task_columns = [
            ("media_type", "VARCHAR(20)"),
            ("media_url", "VARCHAR(500)"),
            ("seen_at", "TIMESTAMP"),
            ("promised_time", "VARCHAR(100)"),
            ("proof_photo_url", "VARCHAR(500)")
        ]

        bool_default = "0" if is_sqlite else "FALSE"
        bool_true_default = "1" if is_sqlite else "TRUE"
        new_loc_columns = [
            ("speed", "FLOAT DEFAULT 0.0"),
            ("battery_level", "INTEGER"),
            ("is_charging", f"BOOLEAN DEFAULT {bool_default}"),
            ("sound_mode", "VARCHAR(20) DEFAULT 'normal'"),
            ("source", "VARCHAR(30) DEFAULT 'telegram_live'"),
            ("is_offline_cached", f"BOOLEAN DEFAULT {bool_default}")
        ]

        new_policy_columns = [
            ("is_all_apps_blocked", f"BOOLEAN DEFAULT {bool_default}"),
            ("is_network_restricted", f"BOOLEAN DEFAULT {bool_default}"),
            ("lockdown_reason", "VARCHAR(100) DEFAULT 'Dars vaqti'"),
            ("blocked_packages", "TEXT DEFAULT '[]'"),
            ("parent_pin_hash", "VARCHAR(128)"),
            ("anti_uninstall_enabled", f"BOOLEAN DEFAULT {bool_true_default}"),
            ("bedtime_start", "VARCHAR(10)"),
            ("bedtime_end", "VARCHAR(10)"),
            ("daily_limit_minutes", "INTEGER")
        ]

        new_user_columns = [
            ("coin_rate_coins", "INTEGER DEFAULT 1000"),
            ("coin_rate_som", "INTEGER DEFAULT 10000"),
            ("web_access_code", "VARCHAR(20)"),
            ("wishlist_title", "VARCHAR(200)"),
            ("wishlist_target_som", "INTEGER DEFAULT 0"),
            ("walk_safe_active", f"BOOLEAN DEFAULT {bool_default}"),
            ("walk_safe_deadline", "TIMESTAMP"),
            ("walk_safe_duration", "INTEGER DEFAULT 0"),
            ("walk_safe_destination", "VARCHAR(100)"),
            ("walk_safe_started_at", "TIMESTAMP"),
            ("walk_safe_last_checkin", "TIMESTAMP")
        ]

        if is_sqlite:
            for col_name, col_type in new_task_columns:
                try:
                    await conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {col_name} {col_type}"))
                except Exception:
                    pass
            for col_name, col_type in new_user_columns:
                try:
                    await conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"))
                except Exception:
                    pass
            for col_name, col_type in new_loc_columns:
                try:
                    await conn.execute(text(f"ALTER TABLE location_logs ADD COLUMN {col_name} {col_type}"))
                except Exception:
                    pass
            for col_name, col_type in new_policy_columns:
                try:
                    await conn.execute(text(f"ALTER TABLE child_device_policies ADD COLUMN {col_name} {col_type}"))
                except Exception:
                    pass
        else:
            for col_name, col_type in new_task_columns:
                try:
                    await conn.execute(text(f"ALTER TABLE tasks ADD COLUMN IF NOT EXISTS {col_name} {col_type}"))
                except Exception as e:
                    logger.debug(f"Task column migration notice ({col_name}): {e}")
            for col_name, col_type in new_user_columns:
                try:
                    await conn.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_type}"))
                except Exception as e:
                    logger.debug(f"User column migration notice ({col_name}): {e}")
            for col_name, col_type in new_loc_columns:
                try:
                    await conn.execute(text(f"ALTER TABLE location_logs ADD COLUMN IF NOT EXISTS {col_name} {col_type}"))
                except Exception as e:
                    logger.debug(f"Location log column migration notice ({col_name}): {e}")
            for col_name, col_type in new_policy_columns:
                try:
                    await conn.execute(text(f"ALTER TABLE child_device_policies ADD COLUMN IF NOT EXISTS {col_name} {col_type}"))
                except Exception as e:
                    logger.debug(f"Device policy column migration notice ({col_name}): {e}")

        # High-performance composite indexes for sub-millisecond queries
        perf_indexes = [
            "CREATE INDEX IF NOT EXISTS ix_loc_child_recorded ON location_logs (child_id, recorded_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_tasks_child_status ON tasks (child_id, status)",
            "CREATE INDEX IF NOT EXISTS ix_geofences_child_active ON geofences (child_id, is_active)"
        ]
        for idx_sql in perf_indexes:
            try:
                await conn.execute(text(idx_sql))
            except Exception:
                pass

    # If running on PostgreSQL, check if auto-seed from SQLite is needed
    if "postgres" in settings.RESOLVED_DATABASE_URL:
        try:
            from db.migration import auto_seed_postgres_from_sqlite
            async with AsyncSessionLocal() as session:
                migrated = await auto_seed_postgres_from_sqlite(session)
                if migrated:
                    logger.info("Successfully migrated existing SQLite records to PostgreSQL!")
        except Exception as e:
            logger.debug(f"Auto-migration check notice: {e}")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI endpoints and standalone scripts."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
