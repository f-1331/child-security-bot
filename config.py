from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
    WEBAPP_BASE_URL: str = "http://localhost:8000"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    DATABASE_URL: str = "sqlite+aiosqlite:///parental_tracker.db"
    TIMEZONE: str = "Asia/Tashkent"
    DEBUG: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def BASE_DIR(self) -> Path:
        return Path(__file__).resolve().parent

    @property
    def WEBAPP_DIR(self) -> Path:
        return self.BASE_DIR / "webapp"


settings = Settings()
