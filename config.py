import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
    WEBAPP_BASE_URL: str = "https://child-security-bot.vercel.app"
    TGCLOUD_APP_TOKEN: Optional[str] = "app8899084854:fQx23Fw3jvg07CZMwsRlO6T24EjFAvzoS4d4O24Uu9A"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    PORT: Optional[int] = None
    DATABASE_URL: str = "sqlite+aiosqlite:///parental_tracker.db"
    TIMEZONE: str = "Asia/Tashkent"
    DEBUG: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def RESOLVED_DATABASE_URL(self) -> str:
        """Resolve database URL for SQLAlchemy async engine.
        Render and cloud providers provide postgres:// or postgresql://,
        which needs to be postgresql+asyncpg:// for async SQLAlchemy.
        """
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and not url.startswith("postgresql+asyncpg://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def PORT_NUMBER(self) -> int:
        """Resolve port dynamically (for cloud platforms like Render/Railway)."""
        if self.PORT is not None:
            return self.PORT
        env_port = os.environ.get("PORT")
        if env_port and env_port.isdigit():
            return int(env_port)
        return self.API_PORT

    @property
    def RESOLVED_WEBAPP_URL(self) -> str:
        """Auto-detect HTTPS domain on Render or Railway."""
        render_url = os.environ.get("RENDER_EXTERNAL_URL")
        if render_url:
            return render_url.rstrip("/")
        railway_url = os.environ.get("RAILWAY_STATIC_URL")
        if railway_url:
            return f"https://{railway_url}".rstrip("/")
        return self.WEBAPP_BASE_URL.rstrip("/")

    @property
    def MINI_APP_URL(self) -> str:
        """URL for Telegram WebApp button with cache-busting version parameter."""
        base = self.RESOLVED_WEBAPP_URL
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}v=13"

    @property
    def BASE_DIR(self) -> Path:
        return Path(__file__).resolve().parent

    @property
    def WEBAPP_DIR(self) -> Path:
        return self.BASE_DIR / "webapp"


settings = Settings()
