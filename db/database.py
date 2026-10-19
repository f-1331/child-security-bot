from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from config import settings


engine = create_async_engine(
    settings.RESOLVED_DATABASE_URL,
    echo=False,
    future=True,
    # if sqlite, need check_same_thread=False
    connect_args={"check_same_thread": False} if "sqlite" in settings.RESOLVED_DATABASE_URL else {}
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    """Initialize database tables schema."""
    async with engine.begin() as conn:
        # Import models so Base metadata has all tables registered
        import db.models  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)

        # Safe automatic migration for new Task columns
        from sqlalchemy import text
        new_columns = [
            ("media_type", "VARCHAR(20)"),
            ("media_url", "VARCHAR(500)"),
            ("seen_at", "TIMESTAMP"),
            ("promised_time", "VARCHAR(100)"),
            ("proof_photo_url", "VARCHAR(500)")
        ]
        for col_name, col_type in new_columns:
            try:
                await conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {col_name} {col_type}"))
            except Exception:
                pass


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
