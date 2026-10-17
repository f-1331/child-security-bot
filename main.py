import asyncio
import logging
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


# Lifespan context manager for FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    prevent_windows_sleep()
    logger.info("Initializing database schema...")
    await init_db()
    logger.info("Database initialized successfully.")
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

# Mount WebApp Static Directory under both /child_security_site and /webapp
webapp_path = Path(__file__).resolve().parent / "webapp"
app.mount("/child_security_site", StaticFiles(directory=str(webapp_path), html=True), name="child_security_site")
app.mount("/webapp", StaticFiles(directory=str(webapp_path), html=True), name="webapp")


@app.api_route("/child_security_site", methods=["GET", "HEAD"], include_in_schema=False)
async def site_path_redirect():
    """Redirect /child_security_site to /child_security_site/index.html."""
    return RedirectResponse(url="/child_security_site/index.html")


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root_redirect():
    """Redirect root to child_security_site dashboard."""
    return RedirectResponse(url="/child_security_site/index.html")


@app.get("/health", tags=["Health"])
async def health_check():
    """Service health status endpoint."""
    return {
        "status": "healthy",
        "service": "Smart Parental Tracker",
        "timezone": settings.TIMEZONE
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
