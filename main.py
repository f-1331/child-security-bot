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
    """Ensure ChildSecurity.apk, ChildSecurity_Parent.apk, and ChildSecurity_Child.apk are available locally for instant bot and web downloads."""
    downloads_dir = Path(settings.BASE_DIR) / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    apk_file = downloads_dir / "ChildSecurity.apk"
    parent_apk = downloads_dir / "ChildSecurity_Parent.apk"
    child_apk = downloads_dir / "ChildSecurity_Child.apk"

    direct_url = "https://github.com/f-1331/child-security-bot/releases/download/v1.0.0-apk/app-debug.apk"
    direct_parent = "https://github.com/f-1331/child-security-bot/releases/download/v1.0.0-apk/ChildSecurity_Parent.apk"
    direct_child = "https://github.com/f-1331/child-security-bot/releases/download/v1.0.0-apk/ChildSecurity_Child.apk"

    import httpx
    import shutil

    # 1. Main APK pre-cache
    if not (apk_file.exists() and apk_file.stat().st_size > 1_000_000):
        try:
            logger.info(f"Downloading APK cache from {direct_url} ...")
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

    # 2. Parent APK pre-cache
    if not (parent_apk.exists() and parent_apk.stat().st_size > 1_000_000):
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                resp = await client.get(direct_parent)
                if resp.status_code == 200 and len(resp.content) > 1_000_000:
                    with open(parent_apk, "wb") as f_out:
                        f_out.write(resp.content)
                    logger.info(f"Downloaded and cached Parent APK ({len(resp.content)} bytes).")
                elif apk_file.exists():
                    shutil.copyfile(apk_file, parent_apk)
        except Exception:
            if apk_file.exists():
                shutil.copyfile(apk_file, parent_apk)

    # 3. Child APK pre-cache
    if not (child_apk.exists() and child_apk.stat().st_size > 1_000_000):
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                resp = await client.get(direct_child)
                if resp.status_code == 200 and len(resp.content) > 1_000_000:
                    with open(child_apk, "wb") as f_out:
                        f_out.write(resp.content)
                    logger.info(f"Downloaded and cached Child APK ({len(resp.content)} bytes).")
                elif apk_file.exists():
                    shutil.copyfile(apk_file, child_apk)
        except Exception:
            if apk_file.exists():
                shutil.copyfile(apk_file, child_apk)

    # 4. iOS MobileConfig Profile pre-cache
    mobileconfig_file = downloads_dir / "ChildSecurity.mobileconfig"
    if not mobileconfig_file.exists():
        try:
            from scripts.generate_mobileconfig import generate
            generate()
            logger.info("Generated Apple iOS mobileconfig installer profile.")
        except Exception as e:
            logger.warning(f"Could not generate iOS mobileconfig: {e}")


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

class SelectiveGZipMiddleware(GZipMiddleware):
    """
    Apply GZip compression only to compressible web assets and JSON payloads.
    Bypasses binary APKs, setup executables, and pre-compressed assets
    so mobile installers and download managers receive raw files with accurate Content-Length.
    """
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            # Exclude download routes and binary archives from GZip compression
            if path.startswith("/download") or path.endswith((".apk", ".bat", ".zip", ".exe", ".gz", ".mobileconfig")):
                await self.app(scope, receive, send)
                return
        await super().__call__(scope, receive, send)


# Enable high-speed GZip compression for responses > 1KB (excluding binary downloads)
app.add_middleware(SelectiveGZipMiddleware, minimum_size=1000)

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


@app.get("/api/system/logs", tags=["System"], include_in_schema=False)
async def get_system_logs():
    """Retrieve recent server execution logs for remote diagnosis."""
    from owner_bot.logger_buffer import get_recent_logs
    return {"logs": get_recent_logs(50)}


@app.get("/api/neon/status", tags=["Neon"])
async def neon_status_endpoint():
    """Verify and return the operational status of wired Neon services."""
    from db.database import AsyncSessionLocal
    from sqlalchemy import text
    import httpx

    status_data = {
        "status": "healthy",
        "branch": settings.NEON_BRANCH or "production",
        "services": {}
    }

    # 1. Neon Postgres
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(text("SELECT current_database(), current_user, version()"))
            db, user, ver = res.fetchone()
            status_data["services"]["postgres"] = {
                "status": "connected",
                "database": db,
                "user": user,
                "version": ver.split()[0] + " " + ver.split()[1]
            }
    except Exception as e:
        status_data["services"]["postgres"] = {"status": "error", "error": str(e)}

    # 2. Neon Auth
    if settings.NEON_AUTH_JWKS_URL:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                jwks_res = await client.get(settings.NEON_AUTH_JWKS_URL)
                status_data["services"]["auth"] = {
                    "status": "active" if jwks_res.status_code == 200 else "degraded",
                    "base_url": settings.NEON_AUTH_BASE_URL,
                    "jwks_status": jwks_res.status_code
                }
        except Exception as e:
            status_data["services"]["auth"] = {"status": "unreachable", "error": str(e)}

    # 3. Neon Functions
    if settings.NEON_FUNCTION_HELLO_BASE_URL:
        try:
            func_url = settings.NEON_FUNCTION_HELLO_BASE_URL
            if not func_url.endswith("/"):
                func_url += "/"
            async with httpx.AsyncClient(timeout=5.0) as client:
                f_res = await client.get(func_url)
                status_data["services"]["functions"] = {
                    "status": "active" if f_res.status_code == 200 else "degraded",
                    "function_url": func_url,
                    "response": f_res.text.strip()
                }
        except Exception as e:
            status_data["services"]["functions"] = {"status": "unreachable", "error": str(e)}

    # 4. Neon Realtime
    if settings.NEON_REALTIME_URL:
        status_data["services"]["realtime"] = {
            "status": "configured",
            "url": settings.NEON_REALTIME_URL
        }

    return status_data

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
        file_size = os.path.getsize(target_apk)
        return FileResponse(
            target_apk,
            filename="ChildSecurity.apk",
            media_type="application/vnd.android.package-archive",
            headers={
                "Content-Length": str(file_size),
                "Content-Disposition": 'attachment; filename="ChildSecurity.apk"',
                "Cache-Control": "no-transform, public, max-age=3600",
                "X-Content-Type-Options": "nosniff"
            }
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
        file_size = os.path.getsize(target)
        return FileResponse(
            target,
            filename="ChildSecurity_Parent.apk",
            media_type="application/vnd.android.package-archive",
            headers={
                "Content-Length": str(file_size),
                "Content-Disposition": 'attachment; filename="ChildSecurity_Parent.apk"',
                "Cache-Control": "no-transform, public, max-age=3600",
                "X-Content-Type-Options": "nosniff"
            }
        )
    return RedirectResponse(
        url="https://github.com/f-1331/child-security-bot/releases/download/v1.0.0-apk/ChildSecurity_Parent.apk",
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
        file_size = os.path.getsize(target)
        return FileResponse(
            target,
            filename="ChildSecurity_Child.apk",
            media_type="application/vnd.android.package-archive",
            headers={
                "Content-Length": str(file_size),
                "Content-Disposition": 'attachment; filename="ChildSecurity_Child.apk"',
                "Cache-Control": "no-transform, public, max-age=3600",
                "X-Content-Type-Options": "nosniff"
            }
        )
    return RedirectResponse(
        url="https://github.com/f-1331/child-security-bot/releases/download/v1.0.0-apk/ChildSecurity_Child.apk",
        status_code=302
    )


@app.get("/download/ios", tags=["App"])
@app.get("/ios", tags=["App"])
async def download_ios():
    """Redirect Apple iOS (iPhone/iPad) users to WebApp PWA installation."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(
        url="https://child-security-bot.vercel.app/#install-ios",
        status_code=302
    )


@app.get("/download/ios-profile", tags=["App"])
@app.get("/download/ios-app", tags=["App"])
async def download_ios_profile():
    """Download official Apple iOS MobileConfig Configuration Profile to install Child Security directly on iPhone."""
    import os
    from fastapi.responses import FileResponse, RedirectResponse
    profile_path = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity.mobileconfig")
    if not os.path.exists(profile_path):
        try:
            from scripts.generate_mobileconfig import generate
            generate()
        except Exception:
            pass
    if os.path.exists(profile_path):
        file_size = os.path.getsize(profile_path)
        return FileResponse(
            profile_path,
            filename="ChildSecurity.mobileconfig",
            media_type="application/x-apple-aspen-config",
            headers={
                "Content-Length": str(file_size),
                "Content-Disposition": 'attachment; filename="ChildSecurity.mobileconfig"',
                "Cache-Control": "no-transform, public, max-age=3600",
                "X-Content-Type-Options": "nosniff"
            }
        )
    return RedirectResponse(url="https://child-security-bot.vercel.app/#install-ios", status_code=302)


@app.get("/download/desktop", tags=["App"])
@app.get("/download/desktop-windows", tags=["App"])
async def download_desktop_windows():
    """Download Windows Desktop standalone launcher setup package."""
    import os
    from fastapi.responses import FileResponse, Response
    desktop_file = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity_Desktop_Setup.bat")
    if os.path.exists(desktop_file):
        return FileResponse(
            desktop_file,
            filename="ChildSecurity_Desktop_Setup.bat",
            media_type="application/x-bat"
        )
    script_content = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "title Child Security - Noutbuk uchun dastur\r\n"
        "cls\r\n"
        "echo ============================================================\r\n"
        "echo          Child Security - Oila va Farzand Nazorati\r\n"
        "echo           Noutbuk va Windows uchun Desktop Dasturi\r\n"
        "echo ============================================================\r\n"
        "echo.\r\n"
        "echo Ish stoliga (Desktop) dastur yorlig'i o'rnatilmoqda...\r\n"
        "set URL=https://child-security-bot.vercel.app\r\n"
        "set \"DESK_PATH=%USERPROFILE%\\Desktop\"\r\n"
        "for /f \"usebackq delims=\" %%d in (`powershell -NoProfile -Command \"[Environment]::GetFolderPath('Desktop')\"`) do (\r\n"
        "    set \"DESK_PATH=%%d\"\r\n"
        ")\r\n"
        "if exist \"%DESK_PATH%\" (\r\n"
        "    echo [InternetShortcut] > \"%DESK_PATH%\\Child Security.url\"\r\n"
        "    echo URL=%URL% >> \"%DESK_PATH%\\Child Security.url\"\r\n"
        "    echo IconIndex=0 >> \"%DESK_PATH%\\Child Security.url\"\r\n"
        "    echo IconFile=%SystemRoot%\\System32\\imageres.dll >> \"%DESK_PATH%\\Child Security.url\"\r\n"
        ")\r\n"
        "if exist \"%USERPROFILE%\\Desktop\" (\r\n"
        "    copy /y \"%DESK_PATH%\\Child Security.url\" \"%USERPROFILE%\\Desktop\\Child Security.url\" >nul 2>&1\r\n"
        ")\r\n"
        "if exist \"%USERPROFILE%\\OneDrive\\Desktop\" (\r\n"
        "    copy /y \"%DESK_PATH%\\Child Security.url\" \"%USERPROFILE%\\OneDrive\\Desktop\\Child Security.url\" >nul 2>&1\r\n"
        ")\r\n"
        "echo.\r\n"
        "echo [OK] Child Security ish stolingizga muvaffaqiyatli o'rnatildi!\r\n"
        "echo Dastur ishga tushirilmoqda...\r\n"
        "echo.\r\n"
        "set \"EDGE_X86=%ProgramFiles(x86)%\\Microsoft\\Edge\\Application\\msedge.exe\"\r\n"
        "set \"EDGE_64=%ProgramFiles%\\Microsoft\\Edge\\Application\\msedge.exe\"\r\n"
        "set \"CHROME_64=%ProgramFiles%\\Google\\Chrome\\Application\\chrome.exe\"\r\n"
        "set \"CHROME_X86=%ProgramFiles(x86)%\\Google\\Chrome\\Application\\chrome.exe\"\r\n"
        "if exist \"%EDGE_X86%\" (\r\n"
        "    start \"\" \"%EDGE_X86%\" --app=\"%URL%\"\r\n"
        "    exit /b 0\r\n"
        ")\r\n"
        "if exist \"%EDGE_64%\" (\r\n"
        "    start \"\" \"%EDGE_64%\" --app=\"%URL%\"\r\n"
        "    exit /b 0\r\n"
        ")\r\n"
        "if exist \"%CHROME_64%\" (\r\n"
        "    start \"\" \"%CHROME_64%\" --app=\"%URL%\"\r\n"
        "    exit /b 0\r\n"
        ")\r\n"
        "if exist \"%CHROME_X86%\" (\r\n"
        "    start \"\" \"%CHROME_X86%\" --app=\"%URL%\"\r\n"
        "    exit /b 0\r\n"
        ")\r\n"
        "start \"\" \"%URL%\"\r\n"
        "exit /b 0\r\n"
    )
    return Response(
        content=script_content.encode("utf-8"),
        media_type="application/x-bat",
        headers={"Content-Disposition": 'attachment; filename="ChildSecurity_Desktop_Setup.bat"'}
    )


@app.get("/download/desktop-mac", tags=["App"])
@app.get("/download/mac", tags=["App"])
async def download_desktop_mac():
    """Download Apple macOS Desktop standalone setup package."""
    import os
    from fastapi.responses import FileResponse, RedirectResponse
    mac_zip = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity_Mac_Setup.zip")
    if not os.path.exists(mac_zip):
        try:
            from scripts.generate_mac_setup import generate
            # generate() can be imported and executed
        except Exception:
            pass
    if os.path.exists(mac_zip):
        return FileResponse(
            mac_zip,
            filename="ChildSecurity_Mac_Setup.zip",
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="ChildSecurity_Mac_Setup.zip"'}
        )
    return RedirectResponse(url="https://child-security-bot.vercel.app/#install-desktop", status_code=302)


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
    is_postgres = "postgres" in settings.RESOLVED_DATABASE_URL.lower()
    return {
        "status": "healthy",
        "service": "Smart Parental Tracker",
        "database_engine": "postgresql" if is_postgres else "sqlite",
        "database_persisted": is_postgres,
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
                BotCommand(command="clean", description="🧹 Chatni tozalash (Eski xabarlarni o'chirish)"),
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

        # Clear webhook and start polling
        await bot.delete_webhook(drop_pending_updates=False)
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
                BotCommand(command="backup", description="💾 Baza zaxirasini yuklab olish"),
                BotCommand(command="clean", description="🧹 Chatni tozalash"),
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

        await bot.delete_webhook(drop_pending_updates=False)
        logger.info(f"Owner Bot polling active for @{owner_info.username}.")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Owner Bot polling error: {e}", exc_info=True)


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
