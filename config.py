import os
import time
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str = "8899084854:AAFwG84zs30n_qbmfJ-XRErYNfyZpKHC6jA"
    BOT_USERNAME: str = "child_security_bot"
    OWNER_BOT_TOKEN: str = "8832152819:AAFTd12XKn1dpXl9mQRhbanEl87CYXTzF68"
    OWNER_USERNAME: str = "fayzbek1331"
    WEBAPP_BASE_URL: str = "https://child-security-bot.vercel.app"
    BACKEND_URL: str = "https://child-security-site.onrender.com"
    TGCLOUD_APP_TOKEN: Optional[str] = "app8899084854:fQx23Fw3jvg07CZMwsRlO6T24EjFAvzoS4d4O24Uu9A"
    OWNER_TGCLOUD_APP_TOKEN: Optional[str] = "app8832152819:GBXPNDMrWyAo1-NmelZJbwnkrGcl_8C1PaPqL_s6Yqw"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    PORT: Optional[int] = None
    DATABASE_URL: str = "sqlite+aiosqlite:///parental_tracker.db"
    TIMEZONE: str = "Asia/Tashkent"
    DEBUG: bool = True
    SENTRY_DSN: Optional[str] = None

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
        """Telegram WebApp frontend URL. Always prioritizes WEBAPP_BASE_URL (Vercel)."""
        if self.WEBAPP_BASE_URL and self.WEBAPP_BASE_URL.strip():
            return self.WEBAPP_BASE_URL.rstrip("/")
        return "https://child-security-bot.vercel.app"

    @property
    def RESOLVED_BACKEND_URL(self) -> str:
        """Backend API host on Render/Railway for API calls, keepalive pings and APK downloads."""
        render_url = os.environ.get("RENDER_EXTERNAL_URL")
        if render_url:
            return render_url.rstrip("/")
        railway_url = os.environ.get("RAILWAY_STATIC_URL")
        if railway_url:
            return f"https://{railway_url}".rstrip("/")
        if self.BACKEND_URL and self.BACKEND_URL.strip():
            return self.BACKEND_URL.rstrip("/")
        return "https://child-security-site.onrender.com"

    @property
    def MINI_APP_URL(self) -> str:
        """Clean official URL for Telegram WebApp button (Vercel)."""
        return self.RESOLVED_WEBAPP_URL

    @property
    def BASE_DIR(self) -> Path:
        return Path(__file__).resolve().parent

    @property
    def WEBAPP_DIR(self) -> Path:
        return self.BASE_DIR / "webapp"


settings = Settings()
