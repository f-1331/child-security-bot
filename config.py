import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
    WEBAPP_BASE_URL: str = "http://localhost:8000"
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
    def BASE_DIR(self) -> Path:
        return Path(__file__).resolve().parent

    @property
    def WEBAPP_DIR(self) -> Path:
        return self.BASE_DIR / "webapp"


settings = Settings()
