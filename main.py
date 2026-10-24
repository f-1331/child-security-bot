import asyncio
import logging
import os
import platform
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import MenuButtonWebApp, WebAppInfo

from config import settings
from db.database import init_db
from bot.handlers import router as bot_router
from api.telemetry import router as telemetry_router, set_bot_instance
from api.webapp_api import router as webapp_router

# Configure logging
logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format="%(asctime)s | %(levelname)-8s | %(name)s : %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("smart_parental_tracker")

# Sentry Monitoring Initialization
sentry_dsn = settings.SENTRY_DSN or os.environ.get("SENTRY_DSN")
if sentry_dsn:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=sentry_dsn,
            environment=os.environ.get("SENTRY_ENVIRONMENT", "production" if not settings.DEBUG else "development"),
            traces_sample_rate=1.0,
            send_default_pii=True,
            enable_logs=True,
        )
        logger.info("Sentry monitoring initialized successfully.")
    except Exception as sentry_err:
        logger.warning(f"Could not initialize Sentry: {sentry_err}")


def prevent_windows_sleep():
    """Prevent Windows from sleeping or throttling background execution when notebook lid is closed."""
    if platform.system() == "Windows":
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_AWAYMODE_REQUIRED = 0x00000040
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
            )
            logger.info("Windows sleep prevention activated (ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED).")
        except Exception as e:
            logger.warning(f"Could not configure Windows sleep prevention: {e}")



async def ensure_cached_apk():
    """Ensure ChildSecurity.apk is available locally for instant bot and web downloads."""
    downloads_dir = Path(settings.BASE_DIR) / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    apk_file = downloads_dir / "ChildSecurity.apk"
    if apk_file.exists() and apk_file.stat().st_size > 1_000_000:
        logger.info(f"Local APK cache verified: {apk_file} ({apk_file.stat().st_size} bytes)")
        return

    direct_url = "https://github.com/f-1331/child-security-bot/releases/download/v1.0.0-apk/app-debug.apk"
    logger.info(f"Downloading APK cache from {direct_url} ...")
    try:
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            resp = await client.get(direct_url)
            if resp.status_code == 200 and len(resp.content) > 1_000_000:
                with open(apk_file, "wb") as f_out:
                    f_out.write(resp.content)
                logger.info(f"Downloaded and cached APK to {apk_file} ({len(resp.content)} bytes).")
            else:
                logger.warning(f"Failed to fetch APK cache: HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"Could not pre-cache APK: {e}")


# Lifespan context manager for FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    prevent_windows_sleep()
    logger.info("Initializing database schema...")
    await init_db()
    logger.info("Database initialized successfully.")
    asyncio.create_task(ensure_cached_apk())
    yield
    logger.info("Application shutting down...")


# Create FastAPI application
app = FastAPI(
    title="Smart Parental Control & Child Location Tracker API",
    description="Production-grade API for Telegram Mini App & Background GPS Telemetry",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for Telegram Mini App WebView
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(telemetry_router)
app.include_router(webapp_router)


@app.get("/api/sentry-debug", tags=["Debug"])
async def trigger_sentry_debug_error():
    """Endpoint for verifying Sentry error monitoring connection."""
    logger.info("Triggering intentional test exception for Sentry verification...")
    division_by_zero = 1 / 0
    return {"result": division_by_zero}

# Mount WebApp Static Directory under both /child_security_site and /webapp
webapp_path = Path(__file__).resolve().parent / "webapp"
app.mount("/child_security_site", StaticFiles(directory=str(webapp_path), html=True), name="child_security_site")
app.mount("/webapp", StaticFiles(directory=str(webapp_path), html=True), name="webapp")

# Mount Uploads directory for task photos and videos
uploads_path = Path(__file__).resolve().parent / "uploads"
uploads_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_path)), name="uploads")


@app.api_route("/child_security_site", methods=["GET", "HEAD"], include_in_schema=False)
async def site_path_redirect():
    """Redirect /child_security_site to /child_security_site/index.html."""
    return RedirectResponse(url="/child_security_site/index.html")


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root_redirect():
    """Redirect root to child_security_site dashboard."""
    return RedirectResponse(url="/child_security_site/index.html")


@app.get("/download/apk", tags=["App"])
async def download_android_apk():
    """Download or redirect to the Android APK file."""
    import os
    from fastapi.responses import FileResponse, RedirectResponse

    downloads_apk = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity.apk")
    local_apk = os.path.join(settings.BASE_DIR, "android", "app", "build", "outputs", "apk", "debug", "app-debug.apk")

    target_apk = None
    if os.path.exists(downloads_apk) and os.path.getsize(downloads_apk) > 1_000_000:
        target_apk = downloads_apk
    elif os.path.exists(local_apk) and os.path.getsize(local_apk) > 1_000_000:
        target_apk = local_apk

    if target_apk:
        return FileResponse(
            target_apk,
            filename="ChildSecurity.apk",
            media_type="application/vnd.android.package-archive"
        )
    return RedirectResponse(
        url="https://github.com/f-1331/child-security-bot/releases/download/v1.0.0-apk/app-debug.apk",
        status_code=302
    )

@app.get("/privacy", tags=["Legal"])
@app.get("/privacy.html", tags=["Legal"])
async def privacy_policy_view():
    """Serve official Google Play compliant Privacy Policy."""
    import os
    from fastapi.responses import FileResponse
    privacy_file = os.path.join(settings.BASE_DIR, "webapp", "privacy.html")
    if os.path.exists(privacy_file):
        return FileResponse(privacy_file, media_type="text/html")
    return RedirectResponse(url="/child_security_site/privacy.html")


@app.get("/health", tags=["Health"])
async def health_check():
    """Service health status endpoint."""
    return {
        "status": "healthy",
        "service": "Smart Parental Tracker",
        "timezone": settings.TIMEZONE
    }


@app.api_route("/api/v1/system/reset-database-fresh", methods=["GET", "POST"], tags=["System"])
async def reset_database_fresh():
    """Wipes all data from database tables so everyone starts completely from scratch."""
    from db.database import engine, Base
    import db.models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    logger.info("DATABASE WAS COMPLETELY RESET FROM SCRATCH BY SYSTEM REQUEST!")
    return {
        "status": "success",
        "message": "All users, links, and logs have been wiped. The database is clean and fresh!"
    }


async def start_uvicorn(server: uvicorn.Server):
    """Run uvicorn server inside asyncio loop."""
    logger.info(f"Starting FastAPI WebApp server on http://{settings.API_HOST}:{settings.PORT_NUMBER}")
    await server.serve()


async def start_bot(bot: Bot, dp: Dispatcher):
    """Run aiogram polling loop."""
    try:
        bot_info = await bot.get_me()
        logger.info(f"Telegram Bot initialized successfully: @{bot_info.username} (ID: {bot_info.id})")
        # Configure persistent Menu button next to chat input to open WebApp
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="🌐 Web App",
                    web_app=WebAppInfo(url=settings.MINI_APP_URL)
                )
            )
            logger.info(f"Telegram chat menu button configured: {settings.MINI_APP_URL}")
        except Exception as e:
            logger.warning(f"Could not configure chat menu button: {e}")

        # Register bot commands menu in Telegram
        try:
            from aiogram.types import BotCommand
            await bot.set_my_commands([
                BotCommand(command="start", description="🔄 Bosh menyu / Botni yangilash"),
                BotCommand(command="apk", description="📲 Android Mobil Ilovani yuklab olish (.apk)"),
                BotCommand(command="app", description="🌐 Web App (Sayt)ni ochish"),
                BotCommand(command="sos", description="🚨 SOS Favqulodda signal"),
                BotCommand(command="help", description="ℹ️ Qo'llanma va yordam"),
            ])
            logger.info("Bot commands registered successfully in Telegram.")
        except Exception as e:
            logger.warning(f"Could not register bot commands: {e}")

        # Drop pending updates to avoid processing stale backlog
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(
            f"Telegram Bot polling error or invalid token: {e}\n"
            f"Please ensure you set a valid BOT_TOKEN in your .env file."
        )


async def render_keepalive():
    """Periodically ping self on Render so the service never sleeps."""
    render_url = settings.RESOLVED_WEBAPP_URL
    if not render_url.startswith("https://"):
        return
    health_url = f"{render_url}/health"
    logger.info(f"Render keepalive service active for {health_url}")
    await asyncio.sleep(60)
    import httpx
    while True:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(health_url)
                logger.info(f"Keepalive ping sent to {health_url} -> {resp.status_code}")
        except Exception as e:
            logger.debug(f"Keepalive ping notice: {e}")
        await asyncio.sleep(600)


async def main():
    """Application main entry point running FastAPI and aiogram concurrently."""
    prevent_windows_sleep()
    logger.info("Starting Smart Parental Control System...")

    # Initialize Database first
    await init_db()

    # Configure Uvicorn server
    config = uvicorn.Config(
        app=app,
        host=settings.API_HOST,
        port=settings.PORT_NUMBER,
        log_level="info" if settings.DEBUG else "warning",
        access_log=False
    )
    server = uvicorn.Server(config)

    # Check if a real bot token was configured
    is_token_configured = (
        bool(settings.BOT_TOKEN)
        and settings.BOT_TOKEN != "YOUR_TELEGRAM_BOT_TOKEN_HERE"
        and ":" in settings.BOT_TOKEN
    )

    bot: Bot | None = None
    tasks = []

    if is_token_configured:
        bot = Bot(
            token=settings.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        set_bot_instance(bot)

        dp = Dispatcher(storage=MemoryStorage())
        dp.include_router(bot_router)

        from bot.scheduler import run_nightly_and_walksafe_worker
        tasks.append(asyncio.create_task(run_nightly_and_walksafe_worker(bot)))

        tasks.append(asyncio.create_task(start_uvicorn(server)))
        tasks.append(asyncio.create_task(start_bot(bot, dp)))
    else:
        logger.warning(
            "========================================================================\n"
            "[NOTICE] BOT_TOKEN is not set or using default placeholder in .env!\n"
            "Telegram Bot polling is paused, but the FastAPI WebApp & Telemetry Server\n"
            f"is actively running on http://localhost:{settings.API_PORT}/webapp/index.html\n"
            "To enable live Telegram interaction, set BOT_TOKEN in your .env file.\n"
            "========================================================================"
        )
        tasks.append(asyncio.create_task(start_uvicorn(server)))
    tasks.append(asyncio.create_task(render_keepalive()))

    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received.")
    finally:
        if server:
            server.should_exit = True
        if bot:
            await bot.session.close()
        logger.info("System gracefully stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Process interrupted by user. Exiting.")
