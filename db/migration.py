import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    User,
    ParentChildLink,
    Geofence,
    LocationLog,
    GeofenceState,
    Task,
    BatteryAlertLog,
)

logger = logging.getLogger("smart_parental_tracker.migration")


def parse_dt(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except Exception:
        try:
            return datetime.strptime(str(val), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None


async def auto_seed_postgres_from_sqlite(session: AsyncSession, sqlite_path: Path | None = None) -> bool:
    """Migrate data from SQLite parental_tracker.db to PostgreSQL if PostgreSQL database is empty."""
    if sqlite_path is None:
        sqlite_path = Path(__file__).resolve().parent.parent / "parental_tracker.db"

    if not sqlite_path.exists():
        logger.info(f"No SQLite database found at {sqlite_path} to migrate.")
        return False

    # Check if target database already has users
    user_count_res = await session.execute(select(func.count(User.id)))
    existing_user_count = user_count_res.scalar() or 0
    if existing_user_count > 0:
        logger.info(f"Target database already contains {existing_user_count} users. Skipping auto-seed.")
        return False

    logger.info(f"Empty database detected. Starting auto-seed from SQLite: {sqlite_path}")

    try:
        conn = sqlite3.connect(str(sqlite_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # 1. Users
        users_rows = c.execute("SELECT * FROM users").fetchall()
        for r in users_rows:
            session.add(User(
                id=r["id"],
                role=r["role"],
                full_name=r["full_name"],
                username=r["username"],
                phone_number=r["phone_number"],
                virtual_balance=r["virtual_balance"] or 0,
                pairing_code=r["pairing_code"],
                pairing_code_created_at=parse_dt(r["pairing_code_created_at"]),
                is_active=bool(r["is_active"]),
                is_sos_active=bool(r["is_sos_active"]),
                created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                updated_at=parse_dt(r["updated_at"]) or datetime.utcnow(),
            ))
        await session.flush()
        logger.info(f"Migrated {len(users_rows)} users.")

        # 2. Parent-Child Links
        links_rows = c.execute("SELECT * FROM parent_child_links").fetchall()
        for r in links_rows:
            session.add(ParentChildLink(
                id=r["id"],
                parent_id=r["parent_id"],
                child_id=r["child_id"],
                nickname=r["nickname"],
                created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
            ))
        await session.flush()
        logger.info(f"Migrated {len(links_rows)} parent_child_links.")

        # 3. Geofences
        geofence_rows = c.execute("SELECT * FROM geofences").fetchall()
        for r in geofence_rows:
            session.add(Geofence(
                id=r["id"],
                parent_id=r["parent_id"],
                child_id=r["child_id"],
                name=r["name"],
                latitude=r["latitude"],
                longitude=r["longitude"],
                radius_meters=r["radius_meters"],
                is_active=bool(r["is_active"]),
                created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
            ))
        await session.flush()
        logger.info(f"Migrated {len(geofence_rows)} geofences.")

        # 4. Location Logs
        loc_rows = c.execute("SELECT * FROM location_logs").fetchall()
        for r in loc_rows:
            session.add(LocationLog(
                id=r["id"],
                child_id=r["child_id"],
                latitude=r["latitude"],
                longitude=r["longitude"],
                accuracy=r["accuracy"],
                battery_level=r["battery_level"],
                is_charging=bool(r["is_charging"]),
                sound_mode=r["sound_mode"],
                source=r["source"],
                recorded_at=parse_dt(r["recorded_at"]) or datetime.utcnow(),
            ))
        await session.flush()
        logger.info(f"Migrated {len(loc_rows)} location_logs.")

        # 5. Geofence States
        try:
            gf_state_rows = c.execute("SELECT * FROM geofence_states").fetchall()
            for r in gf_state_rows:
                session.add(GeofenceState(
                    id=r["id"],
                    geofence_id=r["geofence_id"],
                    child_id=r["child_id"],
                    is_inside=bool(r["is_inside"]),
                    last_transition_at=parse_dt(r["last_transition_at"]) or datetime.utcnow(),
                ))
            await session.flush()
            logger.info(f"Migrated {len(gf_state_rows)} geofence_states.")
        except Exception as e:
            logger.debug(f"geofence_states table skip or error: {e}")

        # 6. Tasks
        try:
            task_rows = c.execute("SELECT * FROM tasks").fetchall()
            for r in task_rows:
                session.add(Task(
                    id=r["id"],
                    parent_id=r["parent_id"],
                    child_id=r["child_id"],
                    title=r["title"],
                    description=r["description"],
                    reward_coins=r["reward_coins"],
                    status=r["status"],
                    created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                    completed_at=parse_dt(r["completed_at"]),
                ))
            await session.flush()
            logger.info(f"Migrated {len(task_rows)} tasks.")
        except Exception as e:
            logger.debug(f"tasks table skip or error: {e}")

        # 7. Battery Alert Logs
        try:
            battery_rows = c.execute("SELECT * FROM battery_alert_logs").fetchall()
            for r in battery_rows:
                session.add(BatteryAlertLog(
                    id=r["id"],
                    child_id=r["child_id"],
                    last_alerted_level=r["last_alerted_level"],
                    last_alerted_at=parse_dt(r["last_alerted_at"]) or datetime.utcnow(),
                ))
            await session.flush()
            logger.info(f"Migrated {len(battery_rows)} battery_alert_logs.")
        except Exception as e:
            logger.debug(f"battery_alert_logs table skip or error: {e}")

        await session.commit()
        conn.close()

        # Update sequences for PostgreSQL tables with serial PK
        try:
            for table_name in ["parent_child_links", "geofences", "location_logs", "geofence_states", "tasks", "battery_alert_logs"]:
                try:
                    await session.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), coalesce(max(id), 1)) FROM {table_name};"
                    ))
                    await session.commit()
                except Exception:
                    pass
        except Exception:
            pass

        logger.info("Auto-seed from SQLite completed successfully!")
        return True

    except Exception as e:
        logger.error(f"Error during auto-seed from SQLite: {e}", exc_info=True)
        await session.rollback()
        return False
