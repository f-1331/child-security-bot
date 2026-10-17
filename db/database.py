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
