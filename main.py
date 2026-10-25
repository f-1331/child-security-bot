import asyncio
import logging
import os
import platform
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
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
import owner_bot.logger_buffer  # noqa: F401
from owner_bot import owner_bot_router, set_main_bot

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

    try:
        import shutil
        parent_apk = downloads_dir / "ChildSecurity_Parent.apk"
        child_apk = downloads_dir / "ChildSecurity_Child.apk"
        if apk_file.exists():
            if not parent_apk.exists() or parent_apk.stat().st_size != apk_file.stat().st_size:
                shutil.copyfile(apk_file, parent_apk)
            if not child_apk.exists() or child_apk.stat().st_size != apk_file.stat().st_size:
                shutil.copyfile(apk_file, child_apk)
    except Exception as e:
        logger.warning(f"Could not duplicate parent/child APKs: {e}")


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

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org https://child-security-bot.vercel.app;"
        )
        response.headers["Permissions-Policy"] = (
            "camera=(self \"https://child-security-bot.vercel.app\"), "
            "geolocation=(self \"https://child-security-bot.vercel.app\" \"https://web.telegram.org\"), "
            "microphone=(self \"https://child-security-bot.vercel.app\")"
        )
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app = FastAPI(
    title="Smart Parental Control & Child Location Tracker API",
    description="Production-grade API for Telegram Mini App & Background GPS Telemetry",
    version="1.0.0",
    lifespan=lifespan
)

# Enable hardened CORS for Telegram Mini App WebView & Frontend Clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://child-security-bot.vercel.app",
        "https://child-security-site.onrender.com",
        "http://localhost:8000",
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:5500",
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)

# Enable high-speed GZip compression for responses > 1KB
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Enterprise Security Headers Middleware
app.add_middleware(SecurityHeadersMiddleware)

# Register API routers
app.include_router(telemetry_router)
app.include_router(webapp_router)


@app.get("/api/sentry-debug", tags=["Debug"], include_in_schema=False)
async def trigger_sentry_debug_error():
    """Endpoint for verifying Sentry error monitoring connection (disabled in production)."""
    if not settings.DEBUG:
        raise HTTPException(status_code=403, detail="Debug endpoint disabled in production")
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


@app.get("/download/parent-apk", tags=["App"])
async def download_parent_apk():
    """Download dedicated Parent Monitoring Android App."""
    import os
    from fastapi.responses import FileResponse, RedirectResponse

    downloads_apk = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity_Parent.apk")
    fallback_apk = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity.apk")
    local_apk = os.path.join(settings.BASE_DIR, "android", "app", "build", "outputs", "apk", "debug", "app-debug.apk")

    target = None
    for path in [downloads_apk, fallback_apk, local_apk]:
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            target = path
            break

    if target:
        return FileResponse(
            target,
            filename="ChildSecurity_Parent.apk",
            media_type="application/vnd.android.package-archive"
        )
    return RedirectResponse(
        url="https://github.com/f-1331/child-security-bot/releases/download/v1.0.0-apk/app-debug.apk",
        status_code=302
    )


@app.get("/download/child-apk", tags=["App"])
async def download_child_apk():
    """Download dedicated Child Safety & 24/7 Background GPS Android App."""
    import os
    from fastapi.responses import FileResponse, RedirectResponse

    downloads_apk = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity_Child.apk")
    fallback_apk = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity.apk")
    local_apk = os.path.join(settings.BASE_DIR, "android", "app", "build", "outputs", "apk", "debug", "app-debug.apk")

    target = None
    for path in [downloads_apk, fallback_apk, local_apk]:
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            target = path
            break

    if target:
        return FileResponse(
            target,
            filename="ChildSecurity_Child.apk",
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
                BotCommand(command="parent_app", description="👨‍👩‍👧 Ota-ona ilovasi (Boshqaruv & Xarita)"),
                BotCommand(command="child_app", description="👦 Farzand ilovasi (24/7 Foniy GPS & SOS)"),
                BotCommand(command="app", description="🌐 Web App (Sayt)ni ochish"),
                BotCommand(command="sos", description="🚨 SOS Favqulodda signal"),
                BotCommand(command="help", description="ℹ️ Qo'llanma va yordam"),
            ])
            logger.info("Bot commands registered successfully in Telegram.")
        except Exception as e:
            logger.warning(f"Could not register bot commands: {e}")

        # Register official bot descriptions in Telegram
        try:
            await bot.set_my_description(
                description=(
                    "🛡️ Smart Parental Control & Farzandlar Xavfsizligi Boti.\n\n"
                    "📍 Farzandingizning jonli joylashuvini 24/7 xaritada kuzating.\n"
                    "🔋 Batareya 20% dan kam qolganda va tezlik oshganda ogohlantirish oling.\n"
                    "🛡️ Kerakli joylar (uy, maktab) geohududlari va nazorat tizimi.\n"
                    "🚨 Favqulodda SOS signallari va 5 soniyalik audio yozuvlar.\n"
                    "💰 Tangalar kursi va rag'batlantiruvchi foydali vazifalar.\n\n"
                    "Boshlash uchun pastdagi «Start» tugmasini bosing!"
                )
            )
            await bot.set_my_short_description(
                short_description="Farzandlar xavfsizligi, 24/7 jonli GPS kuzatuv va SOS himoya tizimi."
            )
            logger.info("Bot official descriptions registered successfully in Telegram.")
        except Exception as e:
            logger.warning(f"Could not register bot descriptions: {e}")

        # Drop pending updates to avoid processing stale backlog
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(
            f"Telegram Bot polling error or invalid token: {e}\n"
            f"Please ensure you set a valid BOT_TOKEN in your .env file."
        )


async def start_owner_bot(bot: Bot, dp: Dispatcher):
    """Run dedicated Owner Bot aiogram polling loop for @fayzbek1331."""
    try:
        owner_info = await bot.get_me()
        logger.info(f"Owner Bot initialized successfully: @{owner_info.username} (ID: {owner_info.id})")
        from aiogram.types import BotCommand
        try:
            await bot.set_my_commands([
                BotCommand(command="start", description="👑 Boshqaruv menyusi"),
                BotCommand(command="stats", description="📊 Tizim statistikasi"),
                BotCommand(command="inspect", description="🔍 ID / username bo'yicha tekshirish"),
                BotCommand(command="sos_list", description="🚨 Faol SOS signallari"),
                BotCommand(command="admins", description="👥 Adminlarni boshqarish"),
                BotCommand(command="broadcast", description="📢 Ommaviy xabar yuborish"),
                BotCommand(command="server", description="🌐 Server & Xavfsizlik holati"),
                BotCommand(command="recent", description="📜 So'nggi foydalanuvchilar"),
                BotCommand(command="logs", description="📑 Server loglari"),
                BotCommand(command="help", description="ℹ️ Qo'llanma"),
            ])
            logger.info("Owner Bot commands registered successfully in Telegram.")
        except Exception as e:
            logger.warning(f"Could not register Owner Bot commands: {e}")

        try:
            await bot.set_my_description(
                description=(
                    "👑 Smart Parental Control — Maxsus Ega va Boshqaruv Boti.\n\n"
                    "Ushbu bot faqat loyiha asoschisi (@fayzbek1331) va tizim administratorlari "
                    "uchun mo'ljallangan yopiq boshqaruv konsolidir."
                )
            )
            await bot.set_my_short_description(
                short_description="Smart Parental Control platformasi yopiq boshqaruv konsoli."
            )
        except Exception as e:
            logger.warning(f"Could not register Owner Bot description: {e}")

        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Owner Bot polling active.")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Owner Bot polling error: {e}")


async def render_keepalive():
    """Periodically ping self on Render so the service never sleeps."""
    render_url = settings.RESOLVED_BACKEND_URL
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
    owner_bot: Bot | None = None
    tasks = []

    if is_token_configured:
        bot = Bot(
            token=settings.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        set_bot_instance(bot)
        set_main_bot(bot)

        dp = Dispatcher(storage=MemoryStorage())
        from bot.anti_ad_middleware import AntiAdShieldMiddleware
        dp.message.outer_middleware(AntiAdShieldMiddleware())
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

    # Start dedicated Owner Bot if configured
    is_owner_token_configured = (
        bool(settings.OWNER_BOT_TOKEN)
        and ":" in settings.OWNER_BOT_TOKEN
    )
    if is_owner_token_configured:
        owner_bot = Bot(
            token=settings.OWNER_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        owner_dp = Dispatcher(storage=MemoryStorage())
        from bot.anti_ad_middleware import AntiAdShieldMiddleware
        owner_dp.message.outer_middleware(AntiAdShieldMiddleware())
        owner_dp.include_router(owner_bot_router)
        tasks.append(asyncio.create_task(start_owner_bot(owner_bot, owner_dp)))
        logger.info("Dedicated Owner Bot task scheduled.")

    tasks.append(asyncio.create_task(render_keepalive()))

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received.")
    except Exception as e:
        logger.error(f"Unexpected error in background service: {e}")
    finally:
        if server:
            server.should_exit = True
        if bot:
            await bot.session.close()
        if owner_bot:
            await owner_bot.session.close()
        logger.info("System gracefully stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Process interrupted by user. Exiting.")
