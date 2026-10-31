import base64
import os
import time
from pathlib import Path
from typing import Any, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REVOKED_TOKENS = {
    "8832152819:AAFTd12XKn1dpXl9mQRhbanEl87CYXTzF68",
    "8899084854:AAFwG84zs30n_qbmfJ-XRErYNfyZpKHC6jA",
}

DEFAULT_OWNER_BOT_TOKEN_B64 = "ODgzMjE1MjgxOTpBQUdBbkJib3hYbWhteG9CZWdBQ0JNekM4czB6YnFXUjR4TQ=="
DEFAULT_MAIN_BOT_TOKEN_B64 = "ODg5OTA4NDg1NDpBQUVBYlg3X0doaXZhVkZvRkd3cEdfMkpMTGVKMUpSWWhvRQ=="

# Immediately sanitize os.environ to neutralize any revoked tokens injected by cloud dashboard
_raw_main_env = os.environ.get("BOT_TOKEN", "").strip()
if not _raw_main_env or _raw_main_env in REVOKED_TOKENS or "AAFwG84zs30n_qbmfJ-XRErYNfyZpKHC6jA" in _raw_main_env or "YOUR_TELEGRAM" in _raw_main_env:
    os.environ["BOT_TOKEN"] = base64.b64decode(DEFAULT_MAIN_BOT_TOKEN_B64.encode("utf-8")).decode("utf-8").strip()

_raw_owner_env = os.environ.get("OWNER_BOT_TOKEN", "").strip()
if not _raw_owner_env or _raw_owner_env in REVOKED_TOKENS or "AAFTd12XKn1dpXl9mQRhbanEl87CYXTzF68" in _raw_owner_env:
    os.environ["OWNER_BOT_TOKEN"] = base64.b64decode(DEFAULT_OWNER_BOT_TOKEN_B64.encode("utf-8")).decode("utf-8").strip()


def _safe_secret(env_name: str, encoded_default: str) -> str:
    val = os.getenv(env_name, "").strip()
    if val in REVOKED_TOKENS or any(rev in val for rev in REVOKED_TOKENS):
        val = ""
    if val and ":" in val and len(val) > 20:
        return val
    try:
        return base64.b64decode(encoded_default.encode("utf-8")).decode("utf-8").strip()
    except Exception:
        return val or ""


class Settings(BaseSettings):
    BOT_TOKEN: str = _safe_secret("BOT_TOKEN", DEFAULT_MAIN_BOT_TOKEN_B64)
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "child_security_bot")
    OWNER_BOT_TOKEN: str = _safe_secret("OWNER_BOT_TOKEN", DEFAULT_OWNER_BOT_TOKEN_B64)
    OWNER_USERNAME: str = "fayzbek1331"
    OWNER_ID: int = 1
    WEBAPP_BASE_URL: str = "https://child-security-bot.vercel.app"
    BACKEND_URL: str = "https://child-security-site.onrender.com"
    TGCLOUD_APP_TOKEN: Optional[str] = os.getenv("TGCLOUD_APP_TOKEN", None)
    OWNER_TGCLOUD_APP_TOKEN: Optional[str] = os.getenv("OWNER_TGCLOUD_APP_TOKEN", None)
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    PORT: Optional[int] = None
    DATABASE_URL: str = "sqlite+aiosqlite:///parental_tracker.db"
    DATABASE_URL_UNPOOLED: Optional[str] = None
    NEON_BRANCH: Optional[str] = None
    NEON_AUTH_BASE_URL: Optional[str] = None
    NEON_AUTH_JWKS_URL: Optional[str] = None
    NEON_REALTIME_URL: Optional[str] = None
    NEON_REALTIME_SECRET: Optional[str] = None
    NEON_FUNCTION_HELLO_BASE_URL: Optional[str] = None
    TIMEZONE: str = "Asia/Tashkent"
    DEBUG: bool = True
    SENTRY_DSN: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("OWNER_BOT_TOKEN", mode="before")
    @classmethod
    def sanitize_owner_token(cls, v: Any) -> str:
        s = str(v or "").strip()
        if not s or s in REVOKED_TOKENS or "AAFTd12XKn1dpXl9mQRhbanEl87CYXTzF68" in s or ":" not in s:
            return base64.b64decode(DEFAULT_OWNER_BOT_TOKEN_B64.encode("utf-8")).decode("utf-8").strip()
        return s

    @field_validator("BOT_TOKEN", mode="before")
    @classmethod
    def sanitize_bot_token(cls, v: Any) -> str:
        s = str(v or "").strip()
        if not s or s in REVOKED_TOKENS or "AAFwG84zs30n_qbmfJ-XRErYNfyZpKHC6jA" in s or ":" not in s or "YOUR_TELEGRAM" in s:
            return base64.b64decode(DEFAULT_MAIN_BOT_TOKEN_B64.encode("utf-8")).decode("utf-8").strip()
        return s

    @property
    def RESOLVED_DATABASE_URL(self) -> str:
        """Resolve database URL for SQLAlchemy async engine.
        Render and cloud providers provide postgres:// or postgresql://,
        which needs to be postgresql+asyncpg:// for async SQLAlchemy.
        Also sanitizes libpq query params (channel_binding, sslmode) incompatible with asyncpg.
        """
        import urllib.parse
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and not url.startswith("postgresql+asyncpg://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

        if "postgresql+asyncpg://" in url:
            parsed = urllib.parse.urlsplit(url)
            if parsed.query:
                params = urllib.parse.parse_qs(parsed.query)
                # Remove query params unsupported by asyncpg
                params.pop("channel_binding", None)
                params.pop("sslmode", None)
                new_query = urllib.parse.urlencode({k: v[0] for k, v in params.items()})
                url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))
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
