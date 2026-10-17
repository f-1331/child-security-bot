"""
Standalone CLI script to migrate all data from SQLite (parental_tracker.db)
to a PostgreSQL database (e.g., Render PostgreSQL, Neon.tech, Supabase).

Usage:
    python migrate_to_postgres.py
    (uses DATABASE_URL from .env or environment)

Or:
    python migrate_to_postgres.py "postgres://user:password@host/dbname"
"""

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from db.database import Base
from db.migration import auto_seed_postgres_from_sqlite


async def run_migration(pg_url: str):
    # Normalize postgres url for asyncpg
    if pg_url.startswith("postgres://"):
        pg_url = pg_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif pg_url.startswith("postgresql://") and not pg_url.startswith("postgresql+asyncpg://"):
        pg_url = pg_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    print(f"\n[1/3] Connecting to target PostgreSQL database...")
    engine = create_async_engine(pg_url, echo=False)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    print(f"[2/3] Creating tables schema in PostgreSQL if not present...")
    async with engine.begin() as conn:
        import db.models  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    print("      Tables verified successfully.")

    print(f"[3/3] Migrating data from local SQLite database (parental_tracker.db)...")
    sqlite_file = Path(__file__).resolve().parent / "parental_tracker.db"
    async with session_factory() as session:
        success = await auto_seed_postgres_from_sqlite(session, sqlite_path=sqlite_file)
        if success:
            print("\n>>> MIGRATION SUCCESSFUL! All records are now stored permanently in PostgreSQL. <<<\n")
        else:
            print("\n>>> NOTICE: Database was already populated or no new records needed to be migrated. <<<\n")

    await engine.dispose()


def main():
    if len(sys.argv) > 1:
        target_url = sys.argv[1].strip()
    else:
        target_url = os.environ.get("DATABASE_URL")

    if not target_url or "sqlite" in target_url:
        print("ERROR: Please provide a valid PostgreSQL URL as argument or in DATABASE_URL environment variable.")
        print("Example: python migrate_to_postgres.py \"postgresql://user:pass@host/dbname\"")
        sys.exit(1)

    asyncio.run(run_migration(target_url))


if __name__ == "__main__":
    main()
