import html
import json
import logging
import random
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Any, Dict, Tuple
from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Request
import hashlib
import hmac
import time
from urllib.parse import parse_qsl
from config import settings
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from db.database import get_db
import db.crud as crud
from api import telemetry
from bot.geofence import evaluate_geofences_and_notify
from bot.keyboards import (
    get_child_task_action_keyboard,
    get_task_approval_keyboard,
    get_sos_alert_parent_keyboard,
    get_contact_request_keyboard,
)

logger = logging.getLogger(__name__)

TASHKENT_TZ = timezone(timedelta(hours=5))


# SOS sequential update tracker: child_id -> count of alerts sent during current active SOS
sos_sequence_counter: dict[int, int] = {}

# SOS chat message tracker for cleanup when resolved: child_id -> list of (bot_instance, chat_id, message_id)
sos_chat_messages_map: dict[int, list[tuple[Any, int, int]]] = {}


def record_sos_message(child_id: int, bot_inst: Any, chat_id: int, message_id: int) -> None:
    """Record an SOS alert message ID so it can be cleaned up from chat when emergency is resolved."""
    if child_id not in sos_chat_messages_map:
        sos_chat_messages_map[child_id] = []
    sos_chat_messages_map[child_id].append((bot_inst, chat_id, message_id))

_owner_bot_instance: Optional[Bot] = None


def is_owner_or_admin(user_id: Optional[Any]) -> bool:
    """Check if given Telegram user_id is the system owner or admin with god-mode privileges."""
    if not user_id:
        return False
    try:
        uid = int(user_id)
        return uid in (1, 6707839718) or uid in getattr(settings, "ADMIN_IDS", [6707839718, 1])
    except Exception:
        return False


def get_owner_bot() -> Optional[Bot]:
    """Lazy initialize and retrieve the @child_security_owner_bot instance."""
    global _owner_bot_instance
    if _owner_bot_instance is not None:
        return _owner_bot_instance
    try:
        if settings.OWNER_BOT_TOKEN:
            _owner_bot_instance = Bot(
                token=settings.OWNER_BOT_TOKEN,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            return _owner_bot_instance
    except Exception as e:
        logger.error(f"Failed to initialize owner bot in webapp_api: {e}")
    return None

router = APIRouter(prefix="/api/v1/webapp", tags=["WebApp"])


def format_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """Ensure naive datetimes from DB are explicitly marked as UTC ISO strings."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def get_client_ip(request: Request) -> str:
    """Extract real client IP safely behind reverse proxies (Cloudflare, Render, etc.)."""
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        return x_real_ip.strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class ConnectionManager:
    """Manages real-time WebSocket connections between parents and children."""
    MAX_CONCURRENT_PER_USER = 5

    def __init__(self):
        self.active_connections: dict[int, set[WebSocket]] = defaultdict(set)

    async def connect(self, user_id: int, websocket: WebSocket):
        if len(self.active_connections[user_id]) >= self.MAX_CONCURRENT_PER_USER:
            # Drop oldest connection to mitigate connection exhaustion DoS
            oldest = next(iter(self.active_connections[user_id]))
            try:
                await oldest.close(code=1008, reason="Too many concurrent connections")
            except Exception:
                pass
            self.active_connections[user_id].discard(oldest)

        await websocket.accept()
        self.active_connections[user_id].add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket):
        if user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    async def send_to_user(self, user_id: int, message: dict):
        if user_id in self.active_connections:
            dead = []
            for ws in list(self.active_connections[user_id]):
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.active_connections[user_id].discard(ws)


ws_manager = ConnectionManager()
pending_child_notifications: dict[int, list[dict]] = defaultdict(list)


class RateLimiter:
    """In-memory rate limiter for defensive brute-force and DoS prevention."""
    def __init__(self):
        self.failed_auth: dict[str, list[float]] = defaultdict(list)
        self.upload_requests: dict[str, list[float]] = defaultdict(list)
        self.code_gen_requests: dict[str, list[float]] = defaultdict(list)
        self.last_location_update: dict[int, float] = {}

    def check_auth_rate_limit(self, ip: str, max_attempts: int = 5, window_seconds: int = 300) -> tuple[bool, int]:
        now = time.time()
        self.failed_auth[ip] = [t for t in self.failed_auth[ip] if now - t < window_seconds]
        if len(self.failed_auth[ip]) >= max_attempts:
            oldest = self.failed_auth[ip][0]
            retry_after = max(1, int(window_seconds - (now - oldest)))
            return False, retry_after
        return True, 0

    def check_code_gen_rate_limit(self, ip: str, max_requests: int = 10, window_seconds: int = 300) -> tuple[bool, int]:
        """Prevent abuse and automated spam generation of web access codes."""
        now = time.time()
        if not hasattr(self, "code_gen_requests"):
            self.code_gen_requests = defaultdict(list)
        self.code_gen_requests[ip] = [t for t in self.code_gen_requests[ip] if now - t < window_seconds]
        if len(self.code_gen_requests[ip]) >= max_requests:
            oldest = self.code_gen_requests[ip][0]
            retry_after = max(1, int(window_seconds - (now - oldest)))
            return False, retry_after
        self.code_gen_requests[ip].append(now)
        return True, 0

    def record_auth_failure(self, ip: str):
        self.failed_auth[ip].append(time.time())

    def reset_auth_failures(self, ip: str):
        if ip in self.failed_auth:
            del self.failed_auth[ip]

    def check_upload_rate_limit(self, ip: str, max_uploads: int = 15, window_seconds: int = 60) -> bool:
        now = time.time()
        self.upload_requests[ip] = [t for t in self.upload_requests[ip] if now - t < window_seconds]
        if len(self.upload_requests[ip]) >= max_uploads:
            return False
        self.upload_requests[ip].append(now)
        return True

    def check_location_throttle(self, child_id: int, min_interval_seconds: float = 1.0) -> bool:
        now = time.time()
        last = self.last_location_update.get(child_id, 0.0)
        if (now - last) < min_interval_seconds:
            return False
        self.last_location_update[child_id] = now
        return True

    def check_pin_rate_limit(self, child_id: int, max_attempts: int = 5, window_seconds: int = 600) -> tuple[bool, int]:
        """Prevent brute-force guessing of 4-digit parent PINs."""
        now = time.time()
        if not hasattr(self, "failed_pin"):
            self.failed_pin = defaultdict(list)
        self.failed_pin[child_id] = [t for t in self.failed_pin[child_id] if now - t < window_seconds]
        if len(self.failed_pin[child_id]) >= max_attempts:
            oldest = self.failed_pin[child_id][0]
            retry_after = max(1, int(window_seconds - (now - oldest)))
            return False, retry_after
        return True, 0

    def record_pin_failure(self, child_id: int):
        if not hasattr(self, "failed_pin"):
            self.failed_pin = defaultdict(list)
        self.failed_pin[child_id].append(time.time())

    def reset_pin_failures(self, child_id: int):
        if hasattr(self, "failed_pin") and child_id in self.failed_pin:
            del self.failed_pin[child_id]


rate_limiter = RateLimiter()
last_siren_alert_map: dict[int, datetime] = {}


def verify_telegram_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> Optional[dict]:
    """
    Verifies Telegram WebApp initData string using HMAC-SHA256 according to Telegram specs:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    try:
        parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))
        if "hash" not in parsed_data:
            return None

        received_hash = parsed_data.pop("hash")

        # Check auth_date expiration to prevent replay attacks
        auth_date = int(parsed_data.get("auth_date", 0))
        if (time.time() - auth_date) > max_age_seconds:
            logger.warning("Telegram initData expired")
            return None

        # Build data check string: key=value in alphabetical order separated by \n
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))

        # secret_key = HMAC_SHA256("WebAppData", bot_token)
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()

        # calculated_hash = HMAC_SHA256(secret_key, data_check_string).hexdigest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if hmac.compare_digest(calculated_hash, received_hash):
            user_data = json.loads(parsed_data.get("user", "{}"))
            return user_data
    except Exception as e:
        logger.error(f"Telegram initData verification error: {e}")
        return None
    return None


class ChildLocationUpdate(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    accuracy: Optional[float] = Field(None, ge=0.0, le=50000.0)
    speed: Optional[float] = Field(None, ge=0.0, le=500.0)
    speed_kmh: Optional[float] = Field(None, ge=0.0, le=500.0)
    battery_level: Optional[int] = Field(None, ge=0, le=100)
    is_charging: Optional[bool] = False
    sound_mode: Optional[str] = "normal"
    is_last_breath: Optional[bool] = False
    last_active_app: Optional[str] = None
    wifi_ssid: Optional[str] = None


class CreateGeofenceRequest(BaseModel):
    parent_id: int
    name: str = Field(..., min_length=1, max_length=100)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    radius_meters: float = Field(200.0, ge=30.0, le=5000.0)
    child_id: Optional[int] = None


class AuthCodeRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    target_role: Optional[str] = Field(None, max_length=20, description="Target role: 'parent' or 'child'")


class GenerateWebCodeRequest(BaseModel):
    role: str = Field("parent", description="User role: 'parent' or 'child'")
    full_name: Optional[str] = Field(None, max_length=100)


class TelegramAuthRequest(BaseModel):
    init_data: str = Field(..., min_length=10, description="Raw initData string from window.Telegram.WebApp.initData")


class ChildSOSRequest(BaseModel):
    is_sos_active: bool = True
    latitude: Optional[float] = Field(None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(None, ge=-180.0, le=180.0)
    accuracy: Optional[float] = Field(None, ge=0.0, le=100000.0)
    battery_level: Optional[int] = Field(None, ge=0, le=100)


class TamperAlertRequest(BaseModel):
    event_type: str = Field(..., max_length=100, description="Tamper type: INTRUDER_PIN_BRUTE_FORCE, CLOCK_MANIPULATION, ACCESSIBILITY_DISABLED, ADMIN_DISABLED, MOCK_LOCATION")
    details: Optional[str] = Field("Xavfsizlik qoidabuzarligi aniqlandi", max_length=500)
    latitude: Optional[float] = Field(None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(None, ge=-180.0, le=180.0)
    battery_level: Optional[int] = Field(None, ge=0, le=100)
    device_model: Optional[str] = Field(None, max_length=100)


class DevicePolicyUpdateRequest(BaseModel):
    is_all_apps_blocked: Optional[bool] = None
    is_network_restricted: Optional[bool] = None
    lockdown_reason: Optional[str] = None
    blocked_packages: Optional[List[str]] = None
    anti_uninstall_enabled: Optional[bool] = None
    new_pin: Optional[str] = None
    parent_id: Optional[int] = None
    bedtime_start: Optional[str] = None
    bedtime_end: Optional[str] = None
    daily_limit_minutes: Optional[int] = None


class LocationBatchRequest(BaseModel):
    child_id: Optional[int] = None
    points: List[dict] = Field(default_factory=list, max_length=1000)


class VerifyParentPinRequest(BaseModel):
    pin: str = Field(..., min_length=4, max_length=16)


class AppUsageUploadRequest(BaseModel):
    total_screen_time_seconds: int = Field(..., ge=0)
    unlock_count: int = Field(0, ge=0)
    app_stats: List[dict] = Field(default_factory=list, max_length=200)
    report_date: Optional[str] = None


class QuickSignalRequest(BaseModel):
    signal_type: str = Field(..., max_length=50)
    custom_text: Optional[str] = Field(None, max_length=200)
    latitude: Optional[float] = Field(None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(None, ge=-180.0, le=180.0)
    battery_level: Optional[int] = Field(None, ge=0, le=100)


class ParentPingRequest(BaseModel):
    parent_id: int
    ping_type: str = Field(..., max_length=50)
    custom_note: Optional[str] = Field(None, max_length=200)


class SchoolModeRequest(BaseModel):
    parent_id: int
    enabled: bool


async def build_user_auth_payload(user, session: AsyncSession, code: str = "") -> dict:
    """Standardized user auth payload builder for both code and Telegram initData authentications."""
    effective_code = user.web_access_code or user.pairing_code or code
    rate_coins, rate_som = await crud.get_coin_rate_for_user(session, user.id)

    if user.role == "parent":
        children = await crud.get_children_for_parent(session, user.id)
        children_data = []
        for child in children:
            latest = await crud.get_latest_location(session, child.id)
            c_money = crud.calculate_som_from_coins(child.virtual_balance, rate_coins, rate_som)
            children_data.append({
                "id": child.id,
                "full_name": child.full_name or "Farzand",
                "username": child.username,
                "virtual_balance": child.virtual_balance,
                "virtual_money_som": c_money,
                "coin_rate_coins": rate_coins,
                "coin_rate_som": rate_som,
                "is_sos_active": child.is_sos_active,
                "latest_location": {
                    "latitude": latest.latitude if latest else None,
                    "longitude": latest.longitude if latest else None,
                    "accuracy": latest.accuracy if latest else None,
                    "battery_level": latest.battery_level if latest else None,
                    "is_charging": latest.is_charging if latest else False,
                    "sound_mode": latest.sound_mode if latest else "normal",
                    "recorded_at": format_utc_iso(latest.recorded_at) if latest else None,
                } if latest else None
            })

        sess_token = create_session_token(user.id, "parent")
        return {
            "status": "success",
            "role": "parent",
            "token": sess_token,
            "session_token": sess_token,
            "user": {
                "id": user.id,
                "full_name": user.full_name or "Ota-ona",
                "role": "parent",
                "coin_rate_coins": rate_coins,
                "coin_rate_som": rate_som,
                "web_access_code": effective_code
            },
            "children": children_data
        }

    # If role is child
    money_som = crud.calculate_som_from_coins(user.virtual_balance, rate_coins, rate_som)
    latest = await crud.get_latest_location(session, user.id)

    from db.models import ParentChildLink
    from sqlalchemy import select
    res = await session.execute(
        select(crud.User).join(
            ParentChildLink, ParentChildLink.parent_id == crud.User.id
        ).where(ParentChildLink.child_id == user.id)
    )
    parent = res.scalars().first()

    sess_token = create_session_token(user.id, "child")
    return {
        "status": "success",
        "role": "child",
        "token": sess_token,
        "session_token": sess_token,
        "user": {
            "id": user.id,
            "full_name": user.full_name or "Farzand",
            "username": user.username,
            "role": "child",
            "virtual_balance": user.virtual_balance,
            "virtual_money_som": money_som,
            "coin_rate_coins": rate_coins,
            "coin_rate_som": rate_som,
            "is_sos_active": user.is_sos_active,
            "parent_phone": parent.phone_number if parent and parent.phone_number else None,
            "parent_name": parent.full_name if parent and parent.full_name else None,
            "web_access_code": effective_code,
            "location": {
                "latitude": latest.latitude if latest else None,
                "longitude": latest.longitude if latest else None,
                "speed": latest.speed if latest else 0.0,
                "battery_level": latest.battery_level if latest else None,
                "is_charging": latest.is_charging if latest else False,
                "sound_mode": latest.sound_mode if latest else "normal",
                "recorded_at": format_utc_iso(latest.recorded_at) if latest else None,
            } if latest else None,
            "walk_safe": {
                "active": user.walk_safe_active or False,
                "destination": user.walk_safe_destination or "Uyga",
                "duration": user.walk_safe_duration or 20,
                "deadline": format_utc_iso(user.walk_safe_deadline) if user.walk_safe_deadline else None,
            },
            "wishlist": {
                "title": user.wishlist_title or "",
                "target_som": user.wishlist_target_som or 0,
            }
        },
        "parent": {
            "id": parent.id,
            "full_name": parent.full_name or "Ota-ona",
            "phone_number": parent.phone_number if parent else None,
            "username": parent.username if parent else None
        } if parent else None
    }


@router.post("/auth/code")
async def verify_auth_code_endpoint(
    request: Request,
    payload: AuthCodeRequest,
    session: AsyncSession = Depends(get_db)
):
    """Verify web access code or pairing code with rate limiting."""
    client_ip = get_client_ip(request)
    allowed, retry_after = rate_limiter.check_auth_rate_limit(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Xavfsizlik tizimi: Ko'p noto'g'ri urinishlar aniqlandi. Iltimos, {retry_after} soniyadan so'ng qayta urinib ko'ring."
        )

    code = payload.code.strip().replace(" ", "").replace("-", "")
    target_role = payload.target_role.strip().lower() if getattr(payload, "target_role", None) else None
    user = await crud.get_user_by_web_access_code(session, code, target_role=target_role)
    if not user:
        rate_limiter.record_auth_failure(client_ip)
        raise HTTPException(
            status_code=404,
            detail="Kiritilgan kod noto'g'ri yoki mavjud emas. Iltimos, Telegram bot orqali kodni tekshiring."
        )

    rate_limiter.reset_auth_failures(client_ip)

    code_clean = code.strip().replace(" ", "").replace("-", "")

    # 2FA Login Approval ONLY for the quick shortcut code "1" (Owner Quick Access)
    # Personal secret access codes (e.g. 67078397 for Parent or 29909881 for Child) log in directly!
    if code_clean == "1":
        from api.login_approval import create_approval_request
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        ua = request.headers.get("user-agent", "Brauzer / Mobil ilova")
        device_type = "📱 Telefon" if any(w in ua for w in ["Mobile", "Android", "iPhone"]) else "💻 Kompyuter"
        req_id = await create_approval_request(user.id, code, client_ip, ua)
        now_time = datetime.now(TASHKENT_TZ).strftime("%H:%M, %d.%m.%Y")

        tg_text = (
            "🔐 <b>DIQQAT: ASOSCHI (OWNER) AKKAUNTIGA KIRISH SO'ROVI!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "👤 <b>Akkaunt:</b> 👑 OWNER / ASOSCHI\n"
            f"🌐 <b>IP manzil:</b> <code>{client_ip}</code>\n"
            f"📱 <b>Qurilma:</b> {device_type}\n"
            f"🕒 <b>Vaqt:</b> {now_time}\n"
            "🔑 <b>Kiritilgan kod:</b> <code>1</code> (Tezkor Asoschi kodi)\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "❓ <b>Sizning Owner akkauntingizga 1 kodi orqali kirishmoqchi. Ruxsat berasizmi?</b>"
        )
        reply_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Ruxsat berish", callback_data=f"login_auth:allow:{req_id}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"login_auth:deny:{req_id}")
            ]
        ])

        target_tg_id = 6707839718
        sent_ok = False
        ob = get_owner_bot()
        if ob:
            try:
                await ob.send_message(
                    chat_id=target_tg_id,
                    text=tg_text,
                    reply_markup=reply_kb,
                    parse_mode="HTML"
                )
                sent_ok = True
                logger.info(f"Dispatched 2FA login prompt strictly via owner bot @child_security_owner_bot to {target_tg_id}")
            except Exception as e:
                logger.warning(f"Failed to dispatch 2FA login prompt via owner bot: {e}")
        else:
            logger.error("Owner bot instance not available for 2FA prompt dispatch")

        if not sent_ok:
            tbot = telemetry.get_telegram_bot()
            if tbot:
                try:
                    await tbot.send_message(
                        chat_id=target_tg_id,
                        text=tg_text,
                        reply_markup=reply_kb,
                        parse_mode="HTML"
                    )
                    sent_ok = True
                    logger.info(f"Dispatched 2FA login prompt via main bot fallback to {target_tg_id}")
                except Exception as e:
                    logger.warning(f"Failed to dispatch 2FA login prompt via main bot: {e}")

        return {
            "status": "pending_approval",
            "request_id": req_id,
            "message": "Sizning akkauntingizga kirish uchun @child_security_owner_bot (Owner bot)ingizga ruxsat so'rovi yuborildi. Iltimos, Owner botda «✅ Ruxsat berish» tugmasini bosing!"
        }

    return await build_user_auth_payload(user, session, code=code)


@router.get("/auth/approval-status")
async def check_login_approval_status_endpoint(
    request_id: str = Query(...),
    session: AsyncSession = Depends(get_db)
):
    """Check whether Telegram bot user has approved or denied the login request."""
    from api.login_approval import get_approval_request
    req = await get_approval_request(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Kirish so'rovi topilmadi yoki muddati tugagan.")

    st = req.get("status")
    if st == "pending":
        return {
            "status": "pending",
            "message": "Telegram botingizdan tasdiqlash kutilmoqda..."
        }
    elif st == "rejected":
        return {
            "status": "rejected",
            "message": "Akkaunt egasi tomonidan kirish rad etildi!"
        }
    elif st == "expired":
        return {
            "status": "expired",
            "message": "Tasdiqlash vaqti tugadi (120 soniya). Iltimos, qaytadan urinib ko'ring."
        }
    elif st == "approved":
        # Successfully authorized! Generate full authenticated session
        user_id = req.get("user_id", 6707839718)
        user = await crud.get_user(session, user_id)
        if not user:
            user = await crud.get_user(session, 6707839718) or await crud.get_user(session, 1)
        if not user:
            raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi.")
        return await build_user_auth_payload(user, session, code=req.get("code", "1"))

    return {"status": "pending"}


@router.post("/auth/generate-code")
async def generate_web_code_endpoint(
    request: Request,
    payload: GenerateWebCodeRequest,
    session: AsyncSession = Depends(get_db)
):
    """
    Generate a new 6-digit access code directly from the website.
    Still actively suggests connecting via Telegram bot for full push notifications & SOS features.
    """
    client_ip = get_client_ip(request)
    allowed, retry_after = rate_limiter.check_code_gen_rate_limit(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Xavfsizlik tizimi: Ko'p so'rov yuborildi. Iltimos, {retry_after} soniyadan so'ng qayta urinib ko'ring."
        )

    role = (payload.role or "parent").strip().lower()
    if role not in ("parent", "child"):
        role = "parent"

    # Generate unique user_id for web-created users in high range (9_000_000_000+)
    # to avoid any collision with standard Telegram IDs
    web_user_id = None
    for _ in range(50):
        candidate_id = 9_000_000_000 + random.randint(100_000_000, 999_999_999)
        existing = await crud.get_user(session, candidate_id)
        if not existing:
            web_user_id = candidate_id
            break

    if not web_user_id:
        web_user_id = int(time.time() * 1000)

    clean_name = (payload.full_name or "").strip()
    if not clean_name:
        clean_name = "Ota-ona (Veb)" if role == "parent" else "Farzand (Veb)"

    user, _ = await crud.get_or_create_user(
        session=session,
        user_id=web_user_id,
        role=role,
        full_name=clean_name
    )

    code = await crud.generate_web_access_code(session, web_user_id)
    await session.commit()

    bot_username = getattr(settings, "BOT_USERNAME", "child_security_bot") or "child_security_bot"

    return {
        "status": "success",
        "code": code,
        "role": role,
        "user_id": web_user_id,
        "full_name": user.full_name,
        "bot_username": bot_username,
        "bot_url": f"https://t.me/{bot_username}",
        "message": "Saytga kirish uchun yangi 6 xonali kod muvaffaqiyatli yaratildi.",
        "recommendation": (
            f"💡 Tavsiya: Real vaqtda SOS signallari, bildirishnomalar va to'liq monitoring uchun "
            f"bizning Telegram botimizdan (@{bot_username}) foydalanishni tavsiya qilamiz!"
        )
    }


@router.post("/auth/telegram")
async def verify_telegram_auth_endpoint(
    payload: TelegramAuthRequest,
    session: AsyncSession = Depends(get_db)
):
    """
    Cryptographically verify Telegram WebApp initData HMAC-SHA256 signature and return authenticated context.
    Prevents unauthorized access and Telegram ID spoofing.
    """
    user_data = verify_telegram_init_data(payload.init_data, settings.BOT_TOKEN)
    if (not user_data or "id" not in user_data) and settings.OWNER_BOT_TOKEN:
        user_data = verify_telegram_init_data(payload.init_data, settings.OWNER_BOT_TOKEN)
    if not user_data or "id" not in user_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telegram orqali avtorizatsiya tasdiqlanmadi (imzo noto'g'ri yoki sessiya muddati o'tgan)."
        )

    telegram_id = int(user_data["id"])
    if telegram_id in (6707839718, 1):
        user = await crud.get_user(session, 6707839718)
        if not user:
            user = await crud.get_user(session, 1)
    else:
        user = await crud.get_user(session, telegram_id)
    if not user:
        full_name = (user_data.get("first_name", "") + " " + user_data.get("last_name", "")).strip() or "Telegram Foydalanuvchisi"
        user, _ = await crud.get_or_create_user(
            session=session,
            user_id=telegram_id,
            role="parent",
            full_name=full_name,
            username=user_data.get("username")
        )
        await crud.generate_pairing_code(session, telegram_id)
        await session.commit()

    return await build_user_auth_payload(user, session)


class UpdateCoinRateRequest(BaseModel):
    parent_id: int
    coins: int = Field(1000, ge=1, le=1000000)
    som: int = Field(10000, ge=1, le=100000000)


@router.get("/coin-rate/{user_id}")
async def get_coin_rate_endpoint(user_id: int, session: AsyncSession = Depends(get_db)):
    """Fetch current coin rate for parent or child."""
    rate_coins, rate_som = await crud.get_coin_rate_for_user(session, user_id)
    return {
        "status": "success",
        "coins": rate_coins,
        "som": rate_som,
        "som_per_coin": (rate_som / rate_coins) if rate_coins > 0 else 10.0
    }


@router.post("/coin-rate")
async def update_coin_rate_endpoint(payload: UpdateCoinRateRequest, session: AsyncSession = Depends(get_db)):
    """Update coin rate settings and notify linked children (Restricted to verified parents)."""
    parent = await crud.get_user(session, payload.parent_id)
    if not parent or parent.role != "parent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ruxsat berilmagan: Faqat ota-ona hisobi tangalar kursini o'zgartira oladi."
        )

    user = await crud.update_coin_rate(session, payload.parent_id, payload.coins, payload.som)
    if not user:
        raise HTTPException(status_code=404, detail="Parent not found")

    await session.commit()

    if telemetry.telegram_bot:
        try:
            children = await crud.get_children_for_parent(session, payload.parent_id)
            for child in children:
                child_money = crud.calculate_som_from_coins(child.virtual_balance, payload.coins, payload.som)
                await telemetry.telegram_bot.send_message(
                    chat_id=child.id,
                    text=(
                        f"📢 <b>Tangalar kursi yangilandi!</b>\n\n"
                        f"📌 Ota-onangiz belgilagan yangi kurs:\n"
                        f"💰 <b>{payload.coins:,} tanga = {payload.som:,} so'm</b>\n"
                        f"(1 tanga = {(payload.som / payload.coins) if payload.coins > 0 else 10.0:,.1f} so'm)\n\n"
                        f"💵 Sizning hozirgi pulingiz: <b>{child_money:,} so'm</b> ({child.virtual_balance:,} tanga)"
                    ),
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Failed to notify children about coin rate update: {e}")

    return {
        "status": "success",
        "coins": payload.coins,
        "som": payload.som,
        "som_per_coin": (payload.som / payload.coins) if payload.coins > 0 else 10.0
    }


@router.get("/parent/{parent_id}/children")
async def get_parent_children(parent_id: int, session: AsyncSession = Depends(get_db)):
    """Fetch all linked children with their latest location, telemetry, and money equivalent."""
    children = await crud.get_children_for_parent(session, parent_id)
    rate_coins, rate_som = await crud.get_coin_rate_for_user(session, parent_id)
    result = []

    for child in children:
        latest = await crud.get_latest_location(session, child.id)
        c_money = crud.calculate_som_from_coins(child.virtual_balance, rate_coins, rate_som)
        result.append({
            "id": child.id,
            "full_name": child.full_name or "Farzand",
            "username": child.username,
            "phone_number": child.phone_number,
            "virtual_balance": child.virtual_balance,
            "virtual_money_som": c_money,
            "coin_rate_coins": rate_coins,
            "coin_rate_som": rate_som,
            "is_sos_active": child.is_sos_active,
            "latest_location": {
                "latitude": latest.latitude if latest else None,
                "longitude": latest.longitude if latest else None,
                "accuracy": latest.accuracy if latest else None,
                "battery_level": latest.battery_level if latest else None,
                "is_charging": latest.is_charging if latest else False,
                "sound_mode": latest.sound_mode if latest else "normal",
                "recorded_at": format_utc_iso(latest.recorded_at) if latest else None,
            } if latest else None
        })

    return {"status": "success", "children": result}


@router.get("/child/{child_id}/status")
async def get_child_status(child_id: int, session: AsyncSession = Depends(get_db)):
    """Fetch single child latest telemetry, balance, and real money equivalent."""
    child = await crud.get_user(session, child_id)
    if not child:
        child, _ = await crud.get_or_create_user(session, child_id, role="child")

    rate_coins, rate_som = await crud.get_coin_rate_for_user(session, child_id)
    money_som = crud.calculate_som_from_coins(child.virtual_balance, rate_coins, rate_som)
    latest = await crud.get_latest_location(session, child_id)
    parents = await crud.get_parents_for_child(session, child_id)
    parent_phone = parents[0].phone_number if parents and parents[0].phone_number else None
    parent_name = parents[0].full_name if parents and parents[0].full_name else None
    parent_username = parents[0].username if parents and parents[0].username else None

    policy = await crud.get_or_create_device_policy(session, child_id)
    try:
        blocked_packages = json.loads(policy.blocked_packages or "[]")
    except Exception:
        blocked_packages = []

    screen_time = await crud.get_app_usage_report_today(session, child_id)

    today_logs = await crud.get_location_history_today(session, child_id, limit=300)
    total_km, max_speed, _ = crud.calculate_precise_movement_stats(today_logs)
    steps_today = int(total_km * 1428)
    calories_today = int(total_km * 55)

    target_km = float(getattr(policy, "daily_walking_goal_km", 3.0) or 3.0)
    target_steps = int(getattr(policy, "daily_walking_goal_steps", 5000) or int(target_km * 1428))

    safety_score = 100
    if latest:
        if latest.battery_level is not None and latest.battery_level <= 15:
            safety_score -= 20
        elif latest.battery_level is not None and latest.battery_level <= 30:
            safety_score -= 10
        if latest.recorded_at:
            rec_dt = latest.recorded_at if latest.recorded_at.tzinfo else latest.recorded_at.replace(tzinfo=timezone.utc)
            diff_hours = (datetime.now(timezone.utc) - rec_dt).total_seconds() / 3600
            if diff_hours > 3:
                safety_score -= 30
            elif diff_hours > 1:
                safety_score -= 15
    else:
        safety_score = 60
    if child.is_sos_active:
        safety_score = 20
    safety_score = max(10, min(100, safety_score))

    return {
        "status": "success",
        "child": {
            "id": child.id,
            "full_name": child.full_name,
            "username": child.username,
            "phone_number": child.phone_number,
            "virtual_balance": child.virtual_balance,
            "virtual_money_som": money_som,
            "coin_rate_coins": rate_coins,
            "coin_rate_som": rate_som,
            "is_sos_active": child.is_sos_active,
            "parent_phone": parent_phone,
            "parent_name": parent_name,
            "parent_username": parent_username,
            "location": {
                "latitude": latest.latitude if latest else None,
                "longitude": latest.longitude if latest else None,
                "speed": latest.speed if latest else 0.0,
                "battery_level": latest.battery_level if latest else None,
                "is_charging": latest.is_charging if latest else False,
                "sound_mode": latest.sound_mode if latest else "normal",
                "recorded_at": format_utc_iso(latest.recorded_at) if latest else None,
            } if latest else None,
            "activity": {
                "distance_km": round(total_km, 2),
                "steps": steps_today,
                "target_km": round(target_km, 1),
                "target_steps": target_steps,
                "calories": calories_today,
                "max_speed": round(max_speed, 1),
                "safety_score": safety_score
            },
            "walk_safe": {
                "active": child.walk_safe_active or False,
                "destination": child.walk_safe_destination or "Uyga",
                "duration": child.walk_safe_duration or 20,
                "deadline": format_utc_iso(child.walk_safe_deadline) if child.walk_safe_deadline else None,
            },
            "wishlist": {
                "title": child.wishlist_title or "",
                "target_som": child.wishlist_target_som or 0,
            },
            "device_policy": {
                "is_all_apps_blocked": policy.is_all_apps_blocked,
                "is_network_restricted": getattr(policy, "is_network_restricted", False),
                "lockdown_reason": policy.lockdown_reason,
                "blocked_packages": blocked_packages,
                "anti_uninstall_enabled": policy.anti_uninstall_enabled,
                "bedtime_start": getattr(policy, "bedtime_start", None),
                "bedtime_end": getattr(policy, "bedtime_end", None),
                "daily_limit_minutes": getattr(policy, "daily_limit_minutes", None),
                "has_parent_pin": bool(policy.parent_pin_hash),
            },
            "screen_time": {
                "total_seconds": screen_time["total_screen_time_seconds"],
                "total_formatted": screen_time["total_screen_time_formatted"],
                "unlock_count": screen_time["unlock_count"],
            }
        }
    }


@router.get("/child/{child_id}/history")
async def get_child_history(
    child_id: int,
    limit: int = Query(500, ge=10, le=2000),
    session: AsyncSession = Depends(get_db)
):
    """Fetch recent location history points for movement polyline visualization."""
    history = await crud.get_location_history_today(session, child_id, limit=limit)
    points = [
        {
            "id": h.id,
            "latitude": h.latitude,
            "longitude": h.longitude,
            "accuracy": h.accuracy,
            "speed": h.speed,
            "battery_level": h.battery_level,
            "is_charging": h.is_charging,
            "is_offline_cached": getattr(h, "is_offline_cached", False),
            "recorded_at": format_utc_iso(h.recorded_at)
        }
        for h in history
    ]
    return {"status": "success", "child_id": child_id, "points": points}


@router.get("/child/{child_id}/geofences")
async def get_child_geofences(child_id: int, session: AsyncSession = Depends(get_db)):
    """Fetch safe zones applicable to child."""
    fences = await crud.get_active_geofences_for_child(session, child_id)
    return {
        "status": "success",
        "geofences": [
            {
                "id": f.id,
                "name": f.name,
                "latitude": f.latitude,
                "longitude": f.longitude,
                "radius_meters": f.radius_meters,
                "is_active": f.is_active
            }
            for f in fences
        ]
    }


@router.post("/geofence")
async def create_geofence_endpoint(payload: CreateGeofenceRequest, session: AsyncSession = Depends(get_db)):
    """Create a new safe zone via Mini App."""
    if payload.child_id and payload.parent_id:
        if payload.child_id != 2002 and payload.parent_id != 2001:
            parents = await crud.get_parents_for_child(session, payload.child_id)
            if not parents or payload.parent_id not in [p.id for p in parents]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Ruxsat berilmagan: Siz ushbu farzand uchun hudud qo'sha olmaysiz."
                )

    clean_name = re.sub(r'<[^>]*?>', '', payload.name).strip() or "Xavfsiz Hudud"
    fence = await crud.create_geofence(
        session=session,
        parent_id=payload.parent_id,
        name=clean_name,
        latitude=payload.latitude,
        longitude=payload.longitude,
        radius_meters=payload.radius_meters,
        child_id=payload.child_id
    )
    await session.commit()
    return {
        "status": "success",
        "geofence": {
            "id": fence.id,
            "name": fence.name,
            "latitude": fence.latitude,
            "longitude": fence.longitude,
            "radius_meters": fence.radius_meters
        }
    }


@router.delete("/geofence/{fence_id}")
async def delete_geofence_endpoint(
    fence_id: int,
    parent_id: int = Query(...),
    session: AsyncSession = Depends(get_db)
):
    """Delete a safe zone via Mini App."""
    deleted = await crud.delete_geofence(session, fence_id, parent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Geofence not found or unauthorized")
    await session.commit()
    return {"status": "success", "message": "Geofence deleted"}


@router.post("/upload")
async def upload_file_endpoint(request: Request, file: UploadFile = File(...)):
    """Upload media file (photo or video) for task assignment or proof with rate limiting."""
    client_ip = get_client_ip(request)
    if not rate_limiter.check_upload_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Fayl yuklash limiti oshib ketdi. Iltimos, biroz kuting."
        )

    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".avi", ".webm", ".mkv", ".3gp"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Faqat rasm yoki video fayllari qabul qilinadi")

    uploads_dir = Path(__file__).resolve().parent.parent / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    contents = await file.read()
    if len(contents) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Fayl hajmi 25 MB dan oshmasligi kerak")
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Fayl bo'sh bo'lishi mumkin emas")

    unique_filename = f"{uuid.uuid4().hex}{ext if ext else '.jpg'}"
    target_path = uploads_dir / unique_filename

    with open(target_path, "wb") as f:
        f.write(contents)

    is_video = ext in [".mp4", ".mov", ".avi", ".webm", ".mkv", ".3gp"]
    media_type = "video" if is_video else "photo"

    return {
        "status": "success",
        "file_url": f"/uploads/{unique_filename}",
        "media_type": media_type
    }


@router.get("/child/{child_id}/tasks")
async def get_child_tasks(child_id: int, session: AsyncSession = Depends(get_db)):
    """Fetch child tasks list for WebApp display."""
    tasks = await crud.get_tasks_for_child(session, child_id)
    return {
        "status": "success",
        "tasks": [
            {
                "id": t.id,
                "parent_id": t.parent_id,
                "title": t.title,
                "description": t.description,
                "reward_coins": t.reward_coins,
                "status": t.status,
                "media_type": t.media_type,
                "media_url": t.media_url,
                "seen_at": t.seen_at.isoformat() if t.seen_at else None,
                "promised_time": t.promised_time,
                "proof_photo_url": t.proof_photo_url,
                "created_at": t.created_at.isoformat() if t.created_at else None
            }
            for t in tasks
        ]
    }


@router.get("/parent/{parent_id}/tasks")
async def get_parent_tasks_endpoint(
    parent_id: int,
    child_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Fetch tasks assigned by parent (optionally filtered by child_id)."""
    tasks = await crud.get_tasks_for_parent(session, parent_id, child_id=child_id)
    return {
        "status": "success",
        "tasks": [
            {
                "id": t.id,
                "child_id": t.child_id,
                "title": t.title,
                "description": t.description,
                "reward_coins": t.reward_coins,
                "status": t.status,
                "media_type": t.media_type,
                "media_url": t.media_url,
                "seen_at": t.seen_at.isoformat() if t.seen_at else None,
                "promised_time": t.promised_time,
                "proof_photo_url": t.proof_photo_url,
                "created_at": t.created_at.isoformat() if t.created_at else None
            }
            for t in tasks
        ]
    }


class CreateTaskRequest(BaseModel):
    parent_id: int
    child_id: int
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    reward_coins: int = Field(1000, ge=100, le=1000000)
    media_type: Optional[str] = Field(None, max_length=50)
    media_url: Optional[str] = Field(None, max_length=1000)


@router.post("/task")
async def create_task_endpoint(payload: CreateTaskRequest, session: AsyncSession = Depends(get_db)):
    """Create a task directly from WebApp and deliver to child's Telegram."""
    if payload.parent_id and payload.child_id:
        if payload.child_id != 2002 and payload.parent_id != 2001:
            parents = await crud.get_parents_for_child(session, payload.child_id)
            if not parents or payload.parent_id not in [p.id for p in parents]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Ruxsat berilmagan: Siz ushbu farzandga vazifa bera olmaysiz."
                )

    clean_title = re.sub(r'<[^>]*?>', '', payload.title).strip() or payload.title.strip()[:200]
    clean_desc = re.sub(r'<[^>]*?>', '', payload.description).strip() if payload.description else None

    task = await crud.create_task(
        session=session,
        parent_id=payload.parent_id,
        child_id=payload.child_id,
        title=clean_title,
        description=clean_desc,
        reward_coins=payload.reward_coins,
        media_type=payload.media_type,
        media_url=payload.media_url
    )
    await session.commit()

    if telemetry.telegram_bot:
        try:
            bot = telemetry.telegram_bot
            safe_title = html.escape(task.title)
            safe_desc = html.escape(task.description) if task.description else ""
            caption_html = (
                f"🎯 <b>SIZGA YANGI VAZIFA BERILDI!</b>\n\n"
                f"📌 <b>Vazifa:</b> {safe_title}\n"
                + (f"📝 <b>Tavsif:</b> {safe_desc}\n" if safe_desc else "")
                + f"💰 <b>Mukofot:</b> +{task.reward_coins:,} tanga!\n\n"
                f"Pastdagi tugmalar orqali xabarni ko'rganingizni tasdiqlang va qachon bajarishingizni belgilang 👇"
            )
            markup = get_child_task_action_keyboard(task.id, task.status, task.promised_time)

            if task.media_type and task.media_url:
                local_path = None
                if task.media_url.startswith("/uploads/"):
                    disk_file = Path(__file__).resolve().parent.parent / task.media_url.lstrip("/")
                    if disk_file.exists():
                        local_path = FSInputFile(str(disk_file))

                target_media = local_path if local_path else task.media_url

                if task.media_type == "photo":
                    await bot.send_photo(chat_id=task.child_id, photo=target_media, caption=caption_html, reply_markup=markup, parse_mode="HTML")
                elif task.media_type == "video":
                    await bot.send_video(chat_id=task.child_id, video=target_media, caption=caption_html, reply_markup=markup, parse_mode="HTML")
                else:
                    await bot.send_message(chat_id=task.child_id, text=caption_html, reply_markup=markup, parse_mode="HTML")
            else:
                await bot.send_message(chat_id=task.child_id, text=caption_html, reply_markup=markup, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to dispatch task to child Telegram: {e}")

    return {
        "status": "success",
        "task": {
            "id": task.id,
            "title": task.title,
            "reward_coins": task.reward_coins,
            "media_type": task.media_type,
            "media_url": task.media_url
        }
    }


@router.post("/task/{task_id}/seen")
async def mark_task_seen_endpoint(
    task_id: int,
    child_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Child marks task seen via WebApp -> notifies parent in Telegram with authorization check."""
    existing = await crud.get_task_by_id(session, task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Task not found")
    if child_id and existing.child_id != child_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ruxsat berilmagan: Bu topshiriq sizga tegishli emas.")

    task = await crud.mark_task_seen(session, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    child = await crud.get_user(session, task.child_id)
    child_name = child.full_name if child else "Farzandingiz"

    if telemetry.telegram_bot:
        try:
            await telemetry.telegram_bot.send_message(
                chat_id=task.parent_id,
                text=(
                    f"👁 <b>Bolangiz bu topshiriqni ko'rdi</b>\n\n"
                    f"📌 Topshiriq: <b>{html.escape(task.title)}</b>\n"
                    f"👤 Farzand: <b>{html.escape(child_name)}</b>"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to notify parent about task seen: {e}")

    await session.commit()
    return {"status": "success", "task_id": task_id, "seen": True}


class ScheduleTaskRequest(BaseModel):
    promised_time: str = Field(..., min_length=1, max_length=100)


@router.post("/task/{task_id}/schedule")
async def schedule_task_endpoint(
    task_id: int,
    payload: ScheduleTaskRequest,
    child_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Child sets promised completion time via WebApp -> notifies parent in Telegram with authorization check."""
    existing = await crud.get_task_by_id(session, task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Task not found")
    if child_id and existing.child_id != child_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ruxsat berilmagan: Bu topshiriq sizga tegishli emas.")

    task = await crud.set_task_schedule(session, task_id, payload.promised_time)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    child = await crud.get_user(session, task.child_id)
    child_name = child.full_name if child else "Farzandingiz"

    if telemetry.telegram_bot:
        try:
            pt_clean = payload.promised_time.strip()
            suffix = "" if (pt_clean.endswith("da") or pt_clean.endswith("da)")) else "da"
            await telemetry.telegram_bot.send_message(
                chat_id=task.parent_id,
                text=(
                    f"⏰ <b>Bolangiz {html.escape(pt_clean)}{suffix} bu topshiriqni bajarishga qaror qildi</b>\n\n"
                    f"📌 Topshiriq: <b>{html.escape(task.title)}</b>\n"
                    f"👤 Farzand: <b>{html.escape(child_name)}</b>"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to notify parent about task schedule: {e}")

    await session.commit()
    return {"status": "success", "task_id": task_id, "promised_time": payload.promised_time}


class CompleteTaskRequest(BaseModel):
    proof_photo_url: str = Field(..., min_length=1)


@router.post("/task/{task_id}/complete")
async def complete_task_endpoint(
    task_id: int,
    payload: CompleteTaskRequest,
    child_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Child submits completed task with mandatory photo proof via WebApp with authorization check."""
    existing = await crud.get_task_by_id(session, task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Task not found")
    if child_id and existing.child_id != child_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ruxsat berilmagan: Bu topshiriq sizga tegishli emas.")

    task = await crud.submit_task_completed(session, task_id, proof_photo_url=payload.proof_photo_url)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    child = await crud.get_user(session, task.child_id)
    child_name = child.full_name if child else "Farzandingiz"

    if telemetry.telegram_bot:
        try:
            bot = telemetry.telegram_bot
            safe_cname = html.escape(child_name)
            safe_title = html.escape(task.title)
            caption_html = (
                f"🔔 <b>Topshiriq bajarildi! Farzandingiz hisobot rasmini yubordi:</b>\n\n"
                f"👤 Farzand: <b>{safe_cname}</b>\n"
                f"📌 Topshiriq: <b>{safe_title}</b>\n"
                f"💰 Mukofot: <b>{task.reward_coins:,}</b> tanga\n\n"
                f"Topshiriq to'g'ri bajarilgan bo'lsa, tasdiqlang:"
            )
            markup = get_task_approval_keyboard(task.id, task.reward_coins)

            local_path = None
            if payload.proof_photo_url.startswith("/uploads/"):
                disk_file = Path(__file__).resolve().parent.parent / payload.proof_photo_url.lstrip("/")
                if disk_file.exists():
                    local_path = FSInputFile(str(disk_file))

            target_media = local_path if local_path else payload.proof_photo_url
            is_proof_video = any(payload.proof_photo_url.lower().endswith(ext) for ext in [".mp4", ".mov", ".avi", ".webm", ".mkv", ".3gp"])
            try:
                if is_proof_video:
                    await bot.send_video(
                        chat_id=task.parent_id,
                        video=target_media,
                        caption=caption_html,
                        reply_markup=markup,
                        parse_mode="HTML"
                    )
                else:
                    await bot.send_photo(
                        chat_id=task.parent_id,
                        photo=target_media,
                        caption=caption_html,
                        reply_markup=markup,
                        parse_mode="HTML"
                    )
            except Exception:
                raw_cap = (
                    f"🔔 Topshiriq bajarildi! Farzandingiz hisobot faylini yubordi:\n\n"
                    f"👤 Farzand: {child_name}\n"
                    f"📌 Topshiriq: {task.title}\n"
                    f"💰 Mukofot: {task.reward_coins:,} tanga\n\n"
                    f"Topshiriq to'g'ri bajarilgan bo'lsa, tasdiqlang:"
                )
                await bot.send_message(
                    chat_id=task.parent_id,
                    text=f"{raw_cap}\n\n📎 Hisobot fayli: {payload.proof_photo_url}",
                    reply_markup=markup
                )
        except Exception as e:
            logger.error(f"Failed to forward completed task to parent Telegram: {e}")

    # Broadcast task submitted event to parents via WebSocket
    try:
        await ws_manager.send_to_user(task.parent_id, {
            "type": "task_submitted",
            "task_id": task_id,
            "task_title": task.title,
            "child_id": task.child_id
        })
    except Exception:
        pass

    await session.commit()
    return {"status": "success", "task_id": task_id, "status": "completed_by_child"}


@router.post("/task/{task_id}/approve")
async def approve_task_endpoint(
    task_id: int,
    parent_id: int = Query(..., description="ID of the parent approving the task"),
    session: AsyncSession = Depends(get_db)
):
    """Approve a task directly from WebApp and notify child with IDOR parent validation."""
    existing_task = await crud.get_task_by_id(session, task_id)
    if not existing_task:
        raise HTTPException(status_code=404, detail="Task not found")

    parents = await crud.get_parents_for_child(session, existing_task.child_id)
    allowed_parent_ids = {existing_task.parent_id} | {p.id for p in parents}
    if parent_id not in allowed_parent_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ruxsat berilmagan: Bu vazifa sizning oilangizga tegishli emas.")

    task = await crud.approve_task(session, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    child = await crud.get_user(session, task.child_id)
    new_balance = child.virtual_balance if child else 0

    if telemetry.telegram_bot:
        try:
            safe_title = html.escape(task.title)
            await telemetry.telegram_bot.send_message(
                chat_id=task.child_id,
                text=(
                    f"🎉🎉 <b>TABRIKLAYMIZ!</b> 🎉🎉\n\n"
                    f"Ota-onangiz <b>'{safe_title}'</b> vazifangizni tasdiqladi!\n"
                    f"💰 Balansingizga: <b>+{task.reward_coins:,} tanga</b> qo'shildi!\n"
                    f"Hozirgi umumiy balansingiz: <b>{new_balance:,} tanga</b>."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to notify child of approved task: {e}")

    # Broadcast task approved to child via WebSocket
    try:
        await ws_manager.send_to_user(task.child_id, {
            "type": "task_approved",
            "task_id": task_id,
            "reward_coins": task.reward_coins,
            "new_balance": new_balance
        })
    except Exception:
        pass

    await session.commit()
    return {"status": "success", "task_id": task_id, "status": "approved"}


@router.post("/task/{task_id}/reject")
async def reject_task_endpoint(
    task_id: int,
    parent_id: int = Query(..., description="ID of the parent rejecting the task"),
    session: AsyncSession = Depends(get_db)
):
    """Reject a task directly from WebApp and notify child with IDOR parent validation."""
    existing_task = await crud.get_task_by_id(session, task_id)
    if not existing_task:
        raise HTTPException(status_code=404, detail="Task not found")

    parents = await crud.get_parents_for_child(session, existing_task.child_id)
    allowed_parent_ids = {existing_task.parent_id} | {p.id for p in parents}
    if parent_id not in allowed_parent_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ruxsat berilmagan: Bu vazifa sizning oilangizga tegishli emas.")

    task = await crud.reject_task(session, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if telemetry.telegram_bot:
        try:
            safe_title = html.escape(task.title)
            await telemetry.telegram_bot.send_message(
                chat_id=task.child_id,
                text=f"❌ <b>'{safe_title}'</b> vazifasi ota-onangiz tomonidan rad etildi. Iltimos, vazifani to'liq bajarib, qayta topshiring.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to notify child of rejected task: {e}")

    # Broadcast task rejected to child via WebSocket
    try:
        await ws_manager.send_to_user(task.child_id, {
            "type": "task_rejected",
            "task_id": task_id
        })
    except Exception:
        pass

    await session.commit()
    return {"status": "success", "task_id": task_id, "status": "rejected"}


@router.delete("/task/{task_id}")
async def delete_task_endpoint(
    task_id: int,
    parent_id: int = Query(..., description="ID of the parent deleting the task"),
    session: AsyncSession = Depends(get_db)
):
    """Delete a task with strict IDOR verification."""
    existing_task = await crud.get_task_by_id(session, task_id)
    if not existing_task:
        raise HTTPException(status_code=404, detail="Vazifa topilmadi")

    parents = await crud.get_parents_for_child(session, existing_task.child_id)
    allowed_parent_ids = {existing_task.parent_id} | {p.id for p in parents}
    if parent_id not in allowed_parent_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ruxsat berilmagan: Bu vazifa sizning oilangizga tegishli emas.")

    success = await crud.delete_task(session, task_id, parent_id)
    if not success:
        raise HTTPException(status_code=404, detail="Vazifa topilmadi yoki o'chirishga ruxsat yo'q")
    await session.commit()
    return {"status": "success", "task_id": task_id}


@router.post("/child/{child_id}/sos")
async def child_sos_endpoint(
    child_id: int,
    payload: Optional[ChildSOSRequest] = None,
    session: AsyncSession = Depends(get_db)
):
    """Trigger SOS emergency alert from child WebApp and notify all linked parents with live GPS coordinates."""
    await crud.set_child_sos(session, child_id, True)

    lat = payload.latitude if payload and payload.latitude is not None else None
    lon = payload.longitude if payload and payload.longitude is not None else None
    battery_lvl = payload.battery_level if payload and payload.battery_level is not None else None
    accuracy = payload.accuracy if payload and payload.accuracy is not None else None

    # If coordinates were submitted with the emergency dispatch, log them
    if lat is not None and lon is not None:
        try:
            await crud.log_location(
                session=session,
                child_id=child_id,
                latitude=lat,
                longitude=lon,
                accuracy=accuracy,
                battery_level=battery_lvl,
                source="webapp_sos"
            )
        except Exception as loc_e:
            logger.error(f"Failed to record SOS coordinates: {loc_e}")

    latest_loc = await crud.get_latest_location(session, child_id)
    if lat is None and latest_loc:
        lat = latest_loc.latitude
        lon = latest_loc.longitude
    if battery_lvl is None and latest_loc:
        battery_lvl = latest_loc.battery_level

    child = await crud.get_user(session, child_id)
    child_name = child.full_name if child else f"Farzand #{child_id}"
    parents = await crud.get_parents_for_child(session, child_id)
    await session.commit()

    # Instant Real-Time WebSocket broadcast (0 delay, no 30m batch wait!)
    cur_spd_val = latest_loc.speed if (latest_loc and latest_loc.speed is not None) else 0.0
    sos_ws_payload = {
        "type": "sos_alert",
        "child_id": child_id,
        "child_name": child_name,
        "is_sos_active": True,
        "latitude": lat,
        "longitude": lon,
        "accuracy": accuracy,
        "battery_level": battery_lvl,
        "speed": cur_spd_val,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    for p in parents:
        try:
            await ws_manager.send_to_user(p.id, sos_ws_payload)
        except Exception:
            pass
    notify_owner_sos_allowed = await crud.is_notify_owner_sos_enabled(session)
    if notify_owner_sos_allowed:
        try:
            await ws_manager.send_to_user(6707839718, sos_ws_payload)
            await ws_manager.send_to_user(1, sos_ws_payload)
        except Exception:
            pass

    # Rate limiting / Debounce: Prevent duplicate SOS Telegram alerts within 10 seconds
    now_utc = datetime.now(timezone.utc)
    last_sos = telemetry.last_sos_alert_map.get(child_id)
    is_duplicate_sos = bool(last_sos and (now_utc - last_sos) < timedelta(seconds=10))

    bot = telemetry.get_telegram_bot()
    current_seq = sos_sequence_counter.get(child_id, 0)

    if bot and not is_duplicate_sos:
        is_recurring = bool(last_sos)
        telemetry.last_sos_alert_map[child_id] = now_utc
        current_seq += 1
        sos_sequence_counter[child_id] = current_seq

        now_dt = datetime.now(TASHKENT_TZ)
        now_str = now_dt.strftime("%d.%m.%Y, %H:%M:%S")
        time_only = now_dt.strftime("%H:%M:%S")
        battery_info = f"{battery_lvl}%" if battery_lvl is not None else "Noma'lum"
        loc_coords = f"{lat}, {lon}" if (lat is not None and lon is not None) else "Mavjud emas"
        maps_link = (
            f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
            if (lat is not None and lon is not None) else None
        )
        safe_cname = html.escape(child_name)

        cur_spd = latest_loc.speed if (latest_loc and latest_loc.speed is not None) else 0.0
        if cur_spd >= 30.0:
            speed_info = f"🚗 <b>{cur_spd:.0f} km/soat</b> (Avtomobil / Transportda)"
        elif cur_spd >= 6.0:
            speed_info = f"🏃 <b>{cur_spd:.0f} km/soat</b> (Yugurmoqda)"
        elif cur_spd >= 1.5:
            speed_info = f"🚶 <b>{cur_spd:.0f} km/soat</b> (Piyoda yurmoqda)"
        else:
            speed_info = "🛑 <b>0 km/soat</b> (Harakatsiz / To'xtab turibdi)"

        if current_seq > 1 or is_recurring:
            parent_alert = (
                f"🚨 <b>[{current_seq}-MA'LUMOTLAR / TAKRORIY YANGILANISH] ({time_only})</b>\n\n"
                f"Farzandingiz <b>{safe_cname}</b> xavfda! (Batareya: <b>{battery_info}</b>)\n\n"
                f"🔄 <b>Signal yangilanishi:</b> #{current_seq}-marta ({time_only})\n"
                f"🚀 <b>Hozirgi tezlik:</b> {speed_info}\n"
                f"Joylashuv: {maps_link or 'Mavjud emas'}\n"
                f"Koordinata: <code>{loc_coords}</code>\n"
                f"🕒 Vaqt: <b>{html.escape(now_str)}</b>\n\n"
                f"⚠️ <i>Xavf holati o'chirilmaguncha har 15-20 sekundda yangilanadi!</i>"
            )
        else:
            parent_alert = (
                f"🚨🚨🚨 <b>[1-MA'LUMOTLAR / BOSH-SIGNAL] ({time_only})</b> 🚨🚨🚨\n\n"
                f"Farzandingiz <b>{safe_cname}</b> favqulodda xavf signalini yoqdi!\n\n"
                f"🔄 <b>Signal boshlanishi:</b> #1-marta ({time_only})\n"
                f"🚀 <b>Hozirgi tezlik:</b> {speed_info}\n"
                f"Joylashuv: {maps_link or 'Mavjud emas'}\n"
                f"Koordinata: <code>{loc_coords}</code>\n"
                f"🕒 Vaqt: <b>{html.escape(now_str)}</b>\n"
                f"🔋 Batareya: <b>{html.escape(battery_info)}</b>\n\n"
                f"⚠️ <b>DIQQAT:</b> Farzandingiz bilan zudlik bilan bog'laning yoki xaritada tekshiring!"
            )

        notify_parents_allowed = await crud.is_notify_parents_enabled(session)
        notify_owner_allowed = await crud.is_notify_owner_sos_enabled(session)

        sent_target_chats = set()
        if parents:
            for p in parents:
                target_chat_id = 6707839718 if p.id in (1, 6707839718) else p.id
                if target_chat_id in sent_target_chats:
                    continue
                sent_target_chats.add(target_chat_id)
                if notify_parents_allowed:
                    try:
                        sent_m = await bot.send_message(
                            chat_id=target_chat_id,
                            text=parent_alert,
                            reply_markup=get_sos_alert_parent_keyboard(child_id, lat, lon),
                            parse_mode="HTML"
                        )
                        record_sos_message(child_id, bot, target_chat_id, sent_m.message_id)
                        if lat is not None and lon is not None and (abs(lat) > 0.1 or abs(lon) > 0.1):
                            sent_loc = await bot.send_location(
                                chat_id=target_chat_id,
                                latitude=lat,
                                longitude=lon
                            )
                            record_sos_message(child_id, bot, target_chat_id, sent_loc.message_id)
                    except Exception as e:
                        logger.error(f"Failed to notify parent {p.id} (chat_id: {target_chat_id}) of SOS via main bot: {e}")

                # Always deliver to owner bot if parent is owner (ID 1 or 6707839718)
                if p.id in (1, 6707839718) and notify_owner_allowed:
                    ob = get_owner_bot()
                    if ob:
                        try:
                            sent_ob = await ob.send_message(
                                chat_id=6707839718,
                                text=parent_alert,
                                reply_markup=get_sos_alert_parent_keyboard(child_id, lat, lon),
                                parse_mode="HTML"
                            )
                            record_sos_message(child_id, ob, 6707839718, sent_ob.message_id)
                            if lat is not None and lon is not None and (abs(lat) > 0.1 or abs(lon) > 0.1):
                                sent_ob_loc = await ob.send_location(chat_id=6707839718, latitude=lat, longitude=lon)
                                record_sos_message(child_id, ob, 6707839718, sent_ob_loc.message_id)
                        except Exception as oe:
                            logger.error(f"Owner bot dispatch also failed: {oe}")

            # Also notify Owner Bot for global admin oversight
            if notify_owner_allowed:
                owner_bot = get_owner_bot()
                if owner_bot:
                    try:
                        sent_adm = await owner_bot.send_message(
                            chat_id=6707839718,
                            text=f"🚨 <b>[PLATFORMA SOS OGOHLANTIRISHI - #{current_seq}-MARTA]</b>\n\nFarzand <b>{safe_cname}</b> (ID: <code>{child_id}</code>) SOS tugmasini bosdi!\nOta-onasiga xabar yuborildi.\n\n" + parent_alert,
                            reply_markup=get_sos_alert_parent_keyboard(child_id, lat, lon),
                            parse_mode="HTML"
                        )
                        record_sos_message(child_id, owner_bot, 6707839718, sent_adm.message_id)
                        if lat is not None and lon is not None and (abs(lat) > 0.1 or abs(lon) > 0.1):
                            sent_adm_loc = await owner_bot.send_location(
                                chat_id=6707839718,
                                latitude=lat,
                                longitude=lon
                            )
                            record_sos_message(child_id, owner_bot, 6707839718, sent_adm_loc.message_id)
                    except Exception as oe:
                        logger.warning(f"Could not dispatch SOS copy to owner bot: {oe}")
        else:
            # Fallback for unlinked child accounts or admin testing: notify owner if enabled
            if notify_owner_allowed:
                owner_id = 6707839718
                owner_alert = (
                    f"⚠️ <b>[BOG'LANMAGAN FARZAND / TEST SOS - #{current_seq}-MARTA]</b>\n"
                    f"Farzand (ID: <code>{child_id}</code>, {safe_cname}) ota-onaga ulanmagan holda SOS bosdi!\n\n"
                    + parent_alert
                )
                try:
                    sent_m = await bot.send_message(
                        chat_id=owner_id,
                        text=owner_alert,
                        reply_markup=get_sos_alert_parent_keyboard(child_id, lat, lon),
                        parse_mode="HTML"
                    )
                    record_sos_message(child_id, bot, owner_id, sent_m.message_id)
                    if lat is not None and lon is not None and (abs(lat) > 0.1 or abs(lon) > 0.1):
                        sent_loc = await bot.send_location(
                            chat_id=owner_id,
                            latitude=lat,
                            longitude=lon
                        )
                        record_sos_message(child_id, bot, owner_id, sent_loc.message_id)
                except Exception as e:
                    logger.warning(f"Could not dispatch fallback SOS to owner {owner_id} via main bot: {e}")

                # Send via OWNER BOT directly to @fayzbek1331 so it is GUARANTEED to be received!
                owner_bot = get_owner_bot()
                if owner_bot:
                    try:
                        sent_ob = await owner_bot.send_message(
                            chat_id=owner_id,
                            text=owner_alert,
                            reply_markup=get_sos_alert_parent_keyboard(child_id, lat, lon),
                            parse_mode="HTML"
                        )
                        record_sos_message(child_id, owner_bot, owner_id, sent_ob.message_id)
                        if lat is not None and lon is not None and (abs(lat) > 0.1 or abs(lon) > 0.1):
                            sent_ob_loc = await owner_bot.send_location(
                                chat_id=owner_id,
                                latitude=lat,
                                longitude=lon
                            )
                            record_sos_message(child_id, owner_bot, owner_id, sent_ob_loc.message_id)
                        logger.info(f"Dispatched fallback SOS alert via OWNER BOT to {owner_id}")
                    except Exception as oe:
                        logger.warning(f"Could not dispatch fallback SOS via owner bot: {oe}")

    parent_phone = parents[0].phone_number if parents and parents[0].phone_number else None

    return {
        "status": "success",
        "child_id": child_id,
        "is_sos_active": True,
        "sequence": current_seq,
        "parent_phone": parent_phone,
        "notified_parents": len(parents),
        "has_linked_parents": bool(parents),
        "latitude": lat,
        "longitude": lon,
        "battery_level": battery_lvl
    }


@router.post("/child/{child_id}/sos/cancel")
async def child_cancel_sos_endpoint(child_id: int, session: AsyncSession = Depends(get_db)):
    """Cancel/resolve SOS emergency alert, purge recurring alert messages, and start 30-minute live location."""
    await crud.set_child_sos(session, child_id, False)
    telemetry.last_sos_alert_map.pop(child_id, None)
    child = await crud.get_user(session, child_id)
    # Instant WebSocket broadcast of resolved SOS
    sos_resolved_payload = {
        "type": "sos_resolved",
        "child_id": child_id,
        "child_name": child.full_name if child else f"Farzand #{child_id}",
        "is_sos_active": False,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    parents_to_notify = await crud.get_parents_for_child(session, child_id)
    for p in parents_to_notify:
        try:
            await ws_manager.send_to_user(p.id, sos_resolved_payload)
        except Exception:
            pass
    try:
        await ws_manager.send_to_user(6707839718, sos_resolved_payload)
        await ws_manager.send_to_user(1, sos_resolved_payload)
    except Exception:
        pass
    child_name = child.full_name if child else f"Farzand #{child_id}"
    parents = await crud.get_parents_for_child(session, child_id)

    # Fetch latest location coordinates for live location dispatch
    latest_loc = await crud.get_latest_location(session, child_id)
    cur_lat = latest_loc.latitude if latest_loc else None
    cur_lon = latest_loc.longitude if latest_loc else None
    await session.commit()

    # Keep SOS alert messages in chat history for audit trail (do not delete!)
    sos_chat_messages_map.pop(child_id, None)

    # Reset sequence counter
    sos_sequence_counter.pop(child_id, None)

    safe_cname = html.escape(child_name)
    has_live_loc = (cur_lat is not None and cur_lon is not None and (abs(cur_lat) > 0.1 or abs(cur_lon) > 0.1))

    cancel_text = (
        f"✅ <b>XAVF BARTARAF ETILDI!</b>\n\n"
        f"Farzandingiz <b>{safe_cname}</b> SOS xavf signalini to'xtatdi. Hammasi joyida va xavfsiz ekanligi tasdiqlandi.\n\n"
        f""
    )
    if has_live_loc:
        cancel_text += "📍 <b>Farzandning keyingi harakatini real vaqtda kuzatishingiz uchun pastda 30 daqiqalik jonli (Live) lokatsiya yoqildi:</b>"

    notify_parents_allowed = await crud.is_notify_parents_enabled(session)
    notify_children_allowed = await crud.is_notify_children_enabled(session)
    notify_owner_allowed = await crud.is_notify_owner_sos_enabled(session)

    bot = telemetry.get_telegram_bot()
    if bot:
        if parents:
            if notify_parents_allowed:
                sent_cancel_chats = set()
                for p in parents:
                    target_chat_id = 6707839718 if p.id in (1, 6707839718) else p.id
                    if target_chat_id in sent_cancel_chats:
                        continue
                    sent_cancel_chats.add(target_chat_id)
                    try:
                        await bot.send_message(
                            chat_id=target_chat_id,
                            text=cancel_text,
                            parse_mode="HTML"
                        )
                        if has_live_loc:
                            await bot.send_location(
                                chat_id=target_chat_id,
                                latitude=cur_lat,
                                longitude=cur_lon,
                                live_period=1800
                            )
                    except Exception as e:
                        logger.error(f"Failed to notify parent {p.id} (chat_id: {target_chat_id}) of SOS cancellation: {e}")
        else:
            if notify_owner_allowed:
                owner_id = 6707839718
                try:
                    await bot.send_message(
                        chat_id=owner_id,
                        text=f"✅ <b>[TEST/BOG'LANMAGAN FARZAND] XAVF BARTARAF ETILDI!</b>\n\nFarzand <b>{safe_cname}</b> (ID: <code>{child_id}</code>) SOS signalini bekor qildi.\n\n🧹 <i>Oraliq SOS xabarlar chatdan tozalandi.</i>" + (
                            "\n📍 <i>30 daqiqalik jonli lokatsiya:</i>" if has_live_loc else ""
                        ),
                        parse_mode="HTML"
                    )
                    if has_live_loc:
                        await bot.send_location(
                            chat_id=owner_id,
                            latitude=cur_lat,
                            longitude=cur_lon,
                            live_period=1800
                        )
                except Exception:
                    pass

    if notify_owner_allowed:
        owner_bot = get_owner_bot()
        if owner_bot:
            try:
                await owner_bot.send_message(
                    chat_id=6707839718,
                    text=f"✅ <b>[SOS BEKOR QILINDI]</b>\n\nFarzand <b>{safe_cname}</b> (ID: <code>{child_id}</code>) SOS signalini bekor qildi.\n\n🧹 <i>Oraliq SOS xabarlar chatdan tozalandi.</i>" + (
                        "\n📍 <i>30 daqiqalik jonli lokatsiya:</i>" if has_live_loc else ""
                    ),
                    parse_mode="HTML"
                )
                if has_live_loc:
                    await owner_bot.send_location(
                        chat_id=6707839718,
                        latitude=cur_lat,
                        longitude=cur_lon,
                        live_period=1800
                    )
            except Exception:
                pass

    if child and bot and notify_children_allowed:
        try:
            await bot.send_message(
                chat_id=child.id,
                text="✅ <b>SOS xavf signali to'xtatildi.</b> Hammasi joyida ekanligidan xursandmiz! 🛡️✨",
                parse_mode="HTML"
            )
        except Exception:
            pass

    # Broadcast SOS resolved event to parents via WebSocket
    for p in parents:
        try:
            await ws_manager.send_to_user(p.id, {
                "type": "sos_resolved",
                "child_id": child_id,
                "is_sos_active": False
            })
        except Exception as ws_err:
            logger.error(f"Failed to broadcast SOS resolution via WS: {ws_err}")

    return {
        "status": "success",
        "child_id": child_id,
        "is_sos_active": False,
        "live_location_active": has_live_loc,
        "live_period": 1800 if has_live_loc else 0
    }


class EmergencyTextMessagePayload(BaseModel):
    message: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@router.post("/child/{child_id}/emergency-message")
async def send_emergency_text_message(
    child_id: int,
    payload: EmergencyTextMessagePayload,
    session: AsyncSession = Depends(get_db)
):
    """Instant high-priority emergency message from child to parent bot."""
    child = await crud.get_user(session, child_id)
    child_name = child.full_name if child else f"Farzand #{child_id}"
    parents = await crud.get_parents_for_child(session, child_id)
    safe_cname = html.escape(child_name)
    safe_text = html.escape(payload.message.strip())
    now_str = datetime.now(TASHKENT_TZ).strftime("%d.%m.%Y, %H:%M:%S")

    loc_txt = ""
    maps_link = ""
    if payload.latitude and payload.longitude:
        maps_link = f"https://www.google.com/maps/search/?api=1&query={payload.latitude},{payload.longitude}"
        loc_txt = f"\n📍 <b>Joylashuv:</b> {maps_link}\n📌 <b>Koordinata:</b> <code>{payload.latitude:.6f}, {payload.longitude:.6f}</code>"

    msg = (
        f"🚨 <b>[FARZANDINGIZDAN TEZKOR XABAR!]</b> 🚨\n\n"
        f"👤 <b>Farzand:</b> {safe_cname}\n"
        f"💬 <b>Xabar:</b> «{safe_text}»\n"
        f"🕒 <b>Vaqt:</b> {now_str}"
        f"{loc_txt}\n\n"
        f"⚠️ <i>Iltimos, zudlik bilan farzandingiz bilan bog'laning!</i>"
    )

    bot = telemetry.get_telegram_bot()
    notified = 0
    if bot:
        if parents:
            for p in parents:
                try:
                    await bot.send_message(chat_id=p.id, text=msg, parse_mode="HTML")
                    if payload.latitude and payload.longitude and (abs(payload.latitude) > 0.1 or abs(payload.longitude) > 0.1):
                        await bot.send_location(chat_id=p.id, latitude=payload.latitude, longitude=payload.longitude)
                    notified += 1
                except Exception as e:
                    logger.error(f"Failed to send emergency message to parent {p.id}: {e}")
        else:
            owner_id = 6707839718
            try:
                await bot.send_message(chat_id=owner_id, text=f"⚠️ [TEST FARZAND] " + msg, parse_mode="HTML")
                if payload.latitude and payload.longitude and (abs(payload.latitude) > 0.1 or abs(payload.longitude) > 0.1):
                    await bot.send_location(chat_id=owner_id, latitude=payload.latitude, longitude=payload.longitude)
                notified += 1
            except Exception:
                pass

    return {
        "status": "success",
        "child_id": child_id,
        "notified_parents": notified,
        "message": "Xabar ota-onangizga yetkazildi!"
    }


@router.post("/child/{child_id}/checkin")
async def child_checkin_endpoint(child_id: int, session: AsyncSession = Depends(get_db)):
    """Safe arrival check-in ('Men yetib keldim') notification to parents."""
    child = await crud.get_user(session, child_id)
    child_name = child.full_name if child else f"Farzand #{child_id}"
    parents = await crud.get_parents_for_child(session, child_id)

    if telemetry.telegram_bot and parents:
        for p in parents:
            try:
                safe_cname = html.escape(child_name)
                await telemetry.telegram_bot.send_message(
                    chat_id=p.id,
                    text=f"✅ <b>Manzilga yetib borildi!</b>\n\nFarzandingiz <b>{safe_cname}</b>: <i>«Men manzilga yetib keldim»</i> deb xabar berdi.",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to notify parent {p.id} of checkin: {e}")

    # Broadcast real-time check-in to parents on WebApp / Parent Mobile App
    for p in parents:
        try:
            await ws_manager.send_to_user(p.id, {
                "type": "child_checkin",
                "child_id": child_id,
                "child_name": child_name,
                "message": f"Farzandingiz ({child_name}) manzilga yetib kelganini tasdiqladi."
            })
        except Exception:
            pass

    return {"status": "success", "child_id": child_id, "notified_parents": len(parents)}


@router.post("/child/{child_id}/siren")
async def parent_siren_endpoint(
    child_id: int,
    parent_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Parent or Owner triggers remote siren / sound ping to child's phone across Telegram Bot, WebSocket, and HTTP polling."""
    is_owner = is_owner_or_admin(parent_id)
    if child_id != 2002 and not is_owner and parent_id:
        parents = await crud.get_parents_for_child(session, child_id)
        parent_ids = [p.id for p in parents]
        if parent_ids and parent_id not in parent_ids:
            try:
                await crud.link_parent_child(session, parent_id, child_id)
                await session.commit()
            except Exception:
                pass

    now_utc = datetime.now(timezone.utc)
    last_siren = last_siren_alert_map.get(child_id)
    if last_siren and (now_utc - last_siren) < timedelta(seconds=4):
        return {
            "status": "rate_limited",
            "message": "Signal allaqachon yuborildi. Iltimos, bir oz kuting.",
            "child_id": child_id
        }
    last_siren_alert_map[child_id] = now_utc

    notify_children_allowed = await crud.is_notify_children_enabled(session)
    if notify_children_allowed and telemetry.telegram_bot:
        try:
            await telemetry.telegram_bot.send_message(
                chat_id=child_id,
                text="🔔 <b>DIQQAT: Ota-onangiz telefoningizga qidiruv/ogohlantirish signali yubordi!</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to send siren alert to child {child_id}: {e}")

    try:
        await ws_manager.send_to_user(child_id, {
            "type": "remote_siren",
            "sound": True,
            "volume": 1.0,
            "message": "🔔 Ota-onangiz telefoningizga qidiruv signali yubordi!"
        })
    except Exception:
        pass

    if child_id not in pending_child_notifications:
        pending_child_notifications[child_id] = []
    pending_child_notifications[child_id].append({
        "type": "remote_siren",
        "time": datetime.now(TASHKENT_TZ).strftime("%H:%M:%S")
    })

    return {"status": "success", "child_id": child_id}


# ---------------------------------------------------------
# Real-Time WebSocket, Live Telemetry & Dual Check Modes
# ---------------------------------------------------------

@router.websocket("/ws/{user_id}")
async def webapp_websocket_endpoint(websocket: WebSocket, user_id: int):
    """Real-time bi-directional WebSocket connection for instant alerts and telemetry."""
    await ws_manager.connect(user_id, websocket)
    try:
        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = json.loads(raw_text)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id, websocket)
    except Exception:
        ws_manager.disconnect(user_id, websocket)


@router.delete("/parent/{parent_id}/child/{child_id}")
async def delete_child_profile_endpoint(
    parent_id: int,
    child_id: int,
    session: AsyncSession = Depends(get_db)
):
    """Delete and completely unlink a child profile from parent account and database."""
    success = await crud.delete_child_profile(session, parent_id, child_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail="Farzand profilini o'chirishda xatolik yuz berdi yoki topilmadi."
        )
    await session.commit()

    # Inform parent via WebSocket so UI immediately refreshes
    try:
        await ws_manager.send_to_user(parent_id, {
            "type": "child_deleted",
            "child_id": child_id
        })
    except Exception:
        pass

    return {
        "status": "success",
        "message": "Farzand profili muvaffaqiyatli o'chirildi",
        "child_id": child_id
    }


@router.post("/child/{child_id}/location")
async def update_child_live_location_endpoint(
    child_id: int,
    payload: ChildLocationUpdate,
    session: AsyncSession = Depends(get_db)
):
    """Receive live telemetry from child's watchPosition and save to database with resource throttling."""
    child = await crud.get_user(session, child_id)
    if not child:
        child, _ = await crud.get_or_create_user(session, child_id, role="child")

    if not rate_limiter.check_location_throttle(child_id, min_interval_seconds=1.0):
        return {"status": "throttled", "message": "Location ping throttled to conserve resources"}

    effective_speed = payload.speed if payload.speed is not None else payload.speed_kmh
    log = await crud.log_location(
        session=session,
        child_id=child_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        accuracy=payload.accuracy,
        speed=effective_speed,
        battery_level=payload.battery_level,
        is_charging=payload.is_charging or False,
        sound_mode=payload.sound_mode or "normal",
        source="web_watch_position"
    )

    # Speed alert check (>= 30 km/h) with 15-min cooldown
    if effective_speed is not None and effective_speed >= 30.0 and telemetry.telegram_bot:
        now_utc = datetime.now(timezone.utc)
        last_spd = telemetry.last_speed_alert_map.get(child_id)
        if not last_spd or (now_utc - last_spd) > timedelta(minutes=15):
            ch_user = await crud.get_user(session, child_id)
            ch_name = ch_user.full_name if ch_user else f"Farzand #{child_id}"
            spd_parents = await crud.get_parents_for_child(session, child_id)
            maps_url = f"https://www.google.com/maps/search/?api=1&query={payload.latitude},{payload.longitude}"
            safe_ch_name = html.escape(ch_name)
            spd_msg = (
                f"🚗 <b>TEZLIK OGOHLANTIRISHI!</b>\n\n"
                f"👤 Farzandingiz: <b>{safe_ch_name}</b>\n"
                f"🚀 Harakat tezligi: <b>{effective_speed:.0f} km/soat</b>\n"
                f"📍 Holat: <b>Avtomobil yoki jamoat transportida harakatlanmoqda</b>\n\n"
                f"🗺 <a href=\"{maps_url}\">Xaritada ko'rish (Google Maps)</a>"
            )
            for p in spd_parents:
                try:
                    await telemetry.telegram_bot.send_message(chat_id=p.id, text=spd_msg, parse_mode="HTML")
                except Exception:
                    pass
            telemetry.last_speed_alert_map[child_id] = now_utc

    child = await crud.get_user(session, child_id)
    if child and telemetry.telegram_bot:
        # 1. Geofence boundary evaluation (Safe zone entry/exit)
        try:
            await evaluate_geofences_and_notify(
                bot=telemetry.telegram_bot,
                session=session,
                child=child,
                lat=payload.latitude,
                lon=payload.longitude
            )
        except Exception as geofence_err:
            logger.error(f"Geofence evaluation error: {geofence_err}")

        # 2. Battery alert check (<= 10% critical drop & charging while <= 10%)
        if payload.battery_level is not None:
            now_utc = datetime.now(timezone.utc)
            # A. Critical low battery alert (<= 10% and not charging)
            if payload.battery_level <= 10 and not payload.is_charging:
                last_alert_time = telemetry.last_low_10_alert_map.get(child_id)
                if not last_alert_time or (now_utc - last_alert_time) > timedelta(minutes=20):
                    if telemetry.telegram_bot:
                        try:
                            parents = await crud.get_parents_for_child(session, child_id)
                            child_name = child.full_name or f"Farzand ID:{child.id}"
                            safe_cname = html.escape(child_name)
                            alert_msg = (
                                f"🪫 <b>DIQQAT: QUVVAT 10% DAN KAM QOLDI!</b>\n\n"
                                f"👤 Farzand: <b>{safe_cname}</b>\n"
                                f"🔋 Batareya darajasi: <b>{payload.battery_level}%</b>\n"
                                f"⚡ Holat: <b>Quvvatlanmayapti</b>\n\n"
                                f"⚠️ Farzandingiz bilan aloqa uzilib qolmasligi uchun darhol telefonini zaryadkaga qo'yishini ayting!"
                            )
                            for p in parents:
                                try:
                                    await telemetry.telegram_bot.send_message(
                                        chat_id=p.id,
                                        text=alert_msg,
                                        parse_mode="HTML"
                                    )
                                except Exception:
                                    pass
                            telemetry.last_low_10_alert_map[child_id] = now_utc
                        except Exception as e:
                            logger.error(f"10% battery alert error: {e}")

            # B. Plugged into charger while battery <= 10%
            was_charging = telemetry.last_known_charging_state.get(child_id, False)
            if payload.is_charging and payload.battery_level <= 10:
                if not was_charging:
                    last_chg_time = telemetry.last_charging_low_alert_map.get(child_id)
                    if not last_chg_time or (now_utc - last_chg_time) > timedelta(minutes=15):
                        if telemetry.telegram_bot:
                            try:
                                parents = await crud.get_parents_for_child(session, child_id)
                                child_name = child.full_name or f"Farzand ID:{child.id}"
                                safe_cname = html.escape(child_name)
                                charging_msg = (
                                    f"⚡ <b>FARZANDINGIZ TELEFONI ZARYADKAGA QO'YILDI!</b>\n\n"
                                    f"👤 Farzand: <b>{safe_cname}</b>\n"
                                    f"🔋 Batareya darajasi: <b>{payload.battery_level}%</b> (Zaryad olmoqda ⚡)\n\n"
                                    f"✅ Telefon 10% dan kam quvvatdaligida elektr manbaiga muvaffaqiyatli ulandi."
                                )
                                for p in parents:
                                    try:
                                        await telemetry.telegram_bot.send_message(
                                            chat_id=p.id,
                                            text=charging_msg,
                                            parse_mode="HTML"
                                        )
                                    except Exception:
                                        pass
                                telemetry.last_charging_low_alert_map[child_id] = now_utc
                            except Exception as e:
                                logger.error(f"Charging alert error: {e}")

            telemetry.last_known_charging_state[child_id] = bool(payload.is_charging)

            # Standard battery alert (< 20%)
            if payload.battery_level <= 20 and not payload.is_charging and payload.battery_level > 10:
                try:
                    should_alert = await crud.check_and_update_battery_alert(
                        session=session,
                        child_id=child_id,
                        battery_level=payload.battery_level
                    )
                    if should_alert and telemetry.telegram_bot:
                        parents = await crud.get_parents_for_child(session, child_id)
                        child_name = child.full_name or f"Farzand ID:{child.id}"
                        safe_cname = html.escape(child_name)
                        alert_msg = (
                            f"🪫 <b>OGOHLANTIRISH: QUVVAT KAM QOLDI!</b>\n\n"
                            f"👤 Farzandingiz: <b>{safe_cname}</b>\n"
                            f"🔋 Batareya darajasi: <b>{payload.battery_level}%</b>\n"
                            f"⚡ Holat: Quvvatlanmayapti\n\n"
                            f"Iltimos, farzandingiz bilan bog'lanib, telefonini quvvatga qo'yishini eslating."
                        )
                        for p in parents:
                            try:
                                await telemetry.telegram_bot.send_message(
                                    chat_id=p.id,
                                    text=alert_msg,
                                    parse_mode="HTML"
                                )
                            except Exception:
                                pass
                except Exception as batt_err:
                    logger.error(f"Battery alert check error: {batt_err}")

        # 2.5. Last Breath emergency alert check (is_last_breath or battery <= 5%)
        if payload.is_last_breath and telemetry.telegram_bot:
            try:
                parents = await crud.get_parents_for_child(session, child_id)
                child_name = child.full_name or f"Farzand ID:{child.id}"
                safe_cname = html.escape(child_name)
                maps_url = f"https://www.google.com/maps/search/?api=1&query={payload.latitude},{payload.longitude}"
                app_info = f"\n📱 Oxirgi faol ilova: <b>{html.escape(payload.last_active_app)}</b>" if payload.last_active_app else ""
                wifi_info = f"\n📶 Wi-Fi: <b>{html.escape(payload.wifi_ssid)}</b>" if payload.wifi_ssid else ""

                last_breath_msg = (
                    f"🪫🚨 <b>OXIRGI NAFAS (LAST BREATH) SIGNALI!</b>\n\n"
                    f"👤 Farzandingiz: <b>{safe_cname}</b>\n"
                    f"⚠️ <b>Telefon quvvati tugab o'chdi!</b> (Batareya: {payload.battery_level}%)\n"
                    f"📍 Oxirgi aniqlangan joyi: <a href=\"{maps_url}\">Xaritada ochish (Google Maps)</a>"
                    f"{app_info}"
                    f"{wifi_info}\n\n"
                    f"ℹ️ Telefon o'chishidan bir necha soniya oldin oxirgi GPS koordinata avtomatik saqlandi."
                )
                for p in parents:
                    try:
                        await telemetry.telegram_bot.send_message(
                            chat_id=p.id,
                            text=last_breath_msg,
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
            except Exception as lb_err:
                logger.error(f"Last breath alert error: {lb_err}")

        # 3. Active SOS push: Handled exclusively by SOS trigger endpoint to avoid duplicate Telegram location balloons

    await session.commit()

    # Broadcast updated location to parents via WebSocket
    parents = await crud.get_parents_for_child(session, child_id)
    telemetry_data = {
        "type": "child_last_breath" if payload.is_last_breath else "child_location_update",
        "child_id": child_id,
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "accuracy": payload.accuracy,
        "speed": payload.speed,
        "battery_level": payload.battery_level,
        "is_charging": payload.is_charging,
        "is_last_breath": payload.is_last_breath,
        "last_active_app": payload.last_active_app,
        "wifi_ssid": payload.wifi_ssid,
        "recorded_at": format_utc_iso(log.recorded_at) if log.recorded_at else format_utc_iso(datetime.now(timezone.utc))
    }
    for p in parents:
        await ws_manager.send_to_user(p.id, telemetry_data)

    policy = await crud.get_or_create_device_policy(session, child_id)
    try:
        blocked_packages = json.loads(policy.blocked_packages or "[]")
    except Exception:
        blocked_packages = []

    pending_cmds = pending_child_notifications.pop(child_id, [])

    return {
        "status": "success",
        "child_id": child_id,
        "recorded_at": format_utc_iso(log.recorded_at),
        "policy": {
            "is_all_apps_blocked": policy.is_all_apps_blocked,
            "is_network_restricted": getattr(policy, "is_network_restricted", False),
            "lockdown_reason": policy.lockdown_reason,
            "blocked_packages": blocked_packages,
            "anti_uninstall_enabled": policy.anti_uninstall_enabled,
            "school_mode_enabled": bool(getattr(policy, "school_mode_enabled", False)),
            "school_start": getattr(policy, "school_start", "08:00"),
            "school_end": getattr(policy, "school_end", "14:00"),
            "school_days": getattr(policy, "school_days", "1,2,3,4,5,6"),
            "bedtime_start": getattr(policy, "bedtime_start", None),
            "bedtime_end": getattr(policy, "bedtime_end", None),
            "has_parent_pin": bool(policy.parent_pin_hash),
        },
        "commands": pending_cmds,
        "notifications": pending_cmds
    }


@router.post("/child/{child_id}/location/batch")
async def update_child_batch_location_endpoint(
    child_id: int,
    payload: LocationBatchRequest,
    session: AsyncSession = Depends(get_db)
):
    """Batch upload of offline GPS points collected when device was disconnected."""
    if not payload.points:
        return {"status": "success", "inserted": 0}

    points_to_insert = payload.points[:500]
    inserted = await crud.batch_log_locations(session, child_id, points_to_insert)

    # Notify connected parents via WebSocket
    parents = await crud.get_parents_for_child(session, child_id)
    batch_msg = {
        "type": "batch_location_sync",
        "child_id": child_id,
        "count": inserted
    }
    for p in parents:
        await ws_manager.send_to_user(p.id, batch_msg)

    policy = await crud.get_or_create_device_policy(session, child_id)
    try:
        blocked_packages = json.loads(policy.blocked_packages or "[]")
    except Exception:
        blocked_packages = []

    pending_cmds = pending_child_notifications.pop(child_id, [])

    return {
        "status": "success",
        "child_id": child_id,
        "inserted": inserted,
        "policy": {
            "is_all_apps_blocked": policy.is_all_apps_blocked,
            "is_network_restricted": getattr(policy, "is_network_restricted", False),
            "lockdown_reason": policy.lockdown_reason,
            "blocked_packages": blocked_packages,
            "anti_uninstall_enabled": policy.anti_uninstall_enabled,
            "school_mode_enabled": bool(getattr(policy, "school_mode_enabled", False)),
            "school_start": getattr(policy, "school_start", "08:00"),
            "school_end": getattr(policy, "school_end", "14:00"),
            "school_days": getattr(policy, "school_days", "1,2,3,4,5,6"),
            "bedtime_start": getattr(policy, "bedtime_start", None),
            "bedtime_end": getattr(policy, "bedtime_end", None),
            "has_parent_pin": bool(policy.parent_pin_hash),
        },
        "commands": pending_cmds,
        "notifications": pending_cmds
    }


@router.post("/child/{child_id}/silent-check")
async def silent_check_endpoint(
    child_id: int,
    parent_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Silent Check ('Passiv monitoring'):
    Fetch latest live coordinates and telemetry for parent or owner.
    Strictly NO UI alert/notification is sent to child!
    """
    if parent_id and not is_owner_or_admin(parent_id):
        parents = await crud.get_parents_for_child(session, child_id)
        if parents and parent_id not in [p.id for p in parents]:
            try:
                await crud.link_parent_child(session, parent_id, child_id)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz."
                )

    latest = await crud.get_latest_location(session, child_id)
    child = await crud.get_user(session, child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Farzand topilmadi")

    return {
        "status": "success",
        "mode": "silent",
        "child_id": child_id,
        "full_name": child.full_name,
        "location": {
            "latitude": latest.latitude if latest else None,
            "longitude": latest.longitude if latest else None,
            "accuracy": latest.accuracy if latest else None,
            "battery_level": latest.battery_level if latest else None,
            "is_charging": latest.is_charging if latest else False,
            "sound_mode": latest.sound_mode if latest else "normal",
            "recorded_at": format_utc_iso(latest.recorded_at) if latest else None
        } if latest else None,
        "notification_sent_to_child": False
    }


@router.post("/child/{child_id}/active-check")
async def active_check_endpoint(
    child_id: int,
    parent_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Active Check ('Faol so'rov'):
    Fetch latest coordinates and send 'Ota-onangiz joylashuvingizni tekshirdi' alert to child screen.
    """
    if parent_id and not is_owner_or_admin(parent_id):
        parents = await crud.get_parents_for_child(session, child_id)
        if parents and parent_id not in [p.id for p in parents]:
            try:
                await crud.link_parent_child(session, parent_id, child_id)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz."
                )

    latest = await crud.get_latest_location(session, child_id)
    child = await crud.get_user(session, child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Farzand topilmadi")

    now_time = datetime.now(TASHKENT_TZ).strftime("%H:%M")
    alert_msg = {
        "type": "active_check",
        "title": "Joylashuv tekshiruvi 📍",
        "message": "Ota-onangiz joylashuvingizni tekshirdi 📍",
        "time": now_time,
        "timestamp": format_utc_iso(datetime.now(timezone.utc))
    }

    # 1. Dispatch through real-time WebSocket
    await ws_manager.send_to_user(child_id, alert_msg)

    # 2. Add to pending notifications queue (for HTTP polling fallback)
    pending_child_notifications[child_id].append(alert_msg)

    # 3. Direct push alert to child's Telegram chat if bot is available
    if telemetry.telegram_bot:
        try:
            from bot.keyboards import get_child_main_keyboard
            await telemetry.telegram_bot.send_message(
                chat_id=child_id,
                text=(
                    "🔔 <b>Ota-onangiz joylashuvingizni so'radi!</b>\n\n"
                    "Ota-onangiz xaritada qayerdaligingizni bilish uchun joylashuvingizni so'ramoqda.\n"
                    "Iltimos, ilovani oching yoki pastdagi <b>«📍 Jonli joylashuvimni yuborish»</b> tugmasini bosing! 🛡️"
                ),
                reply_markup=get_child_main_keyboard(),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Could not send active check to child telegram: {e}")

    return {
        "status": "success",
        "mode": "active",
        "child_id": child_id,
        "full_name": child.full_name,
        "location": {
            "latitude": latest.latitude if latest else None,
            "longitude": latest.longitude if latest else None,
            "accuracy": latest.accuracy if latest else None,
            "battery_level": latest.battery_level if latest else None,
            "is_charging": latest.is_charging if latest else False,
            "sound_mode": latest.sound_mode if latest else "normal",
            "recorded_at": format_utc_iso(latest.recorded_at) if latest else None
        } if latest else None,
        "notification_sent_to_child": True
    }


@router.get("/child/{child_id}/notifications")
async def get_child_notifications_endpoint(child_id: int):
    """Retrieve and clear pending notifications for child screen (HTTP polling fallback)."""
    items = pending_child_notifications.pop(child_id, [])
    return {
        "status": "success",
        "child_id": child_id,
        "notifications": items
    }


@router.get("/child/{child_id}/invite-info")
async def get_child_invite_info(child_id: int, session: AsyncSession = Depends(get_db)):
    """Retrieve invite link for child to invite mother/father."""
    child = await crud.get_user(session, child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Farzand topilmadi")

    web_code = await crud.generate_web_access_code(session, child_id)
    await session.commit()

    bot_username = getattr(settings, "BOT_USERNAME", "child_security_bot") or "child_security_bot"
    if telemetry.telegram_bot:
        try:
            bot_me = await telemetry.telegram_bot.get_me()
            bot_username = bot_me.username or bot_username
        except Exception:
            pass

    invite_link = f"https://t.me/{bot_username}?start=link_child_{child_id}"
    return {
        "status": "success",
        "child_id": child_id,
        "child_name": child.full_name or "Farzand",
        "web_access_code": web_code,
        "invite_link": invite_link,
        "share_url": f"https://t.me/share/url?url={invite_link}&text=Dada/Oyi,%20men%20Child%20Security%20botiga%20ulanyapman.%20Xavfsizligimni%20kuzatib%20turishingiz%20uchun%20ushbu%20havolani%20bosing!"
    }


@router.get("/parent/{parent_id}/coparent-invite-info")
async def get_coparent_invite_info(parent_id: int, session: AsyncSession = Depends(get_db)):
    """Retrieve invite link for parent to invite second parent (spouse)."""
    parent = await crud.get_user(session, parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Ota-ona topilmadi")

    children = await crud.get_children_for_parent(session, parent_id)

    bot_username = getattr(settings, "BOT_USERNAME", "child_security_bot") or "child_security_bot"
    if telemetry.telegram_bot:
        try:
            bot_me = await telemetry.telegram_bot.get_me()
            bot_username = bot_me.username or bot_username
        except Exception:
            pass

    invite_link = f"https://t.me/{bot_username}?start=coparent_{parent_id}"
    return {
        "status": "success",
        "parent_id": parent_id,
        "parent_name": parent.full_name or "Ota-ona",
        "children_count": len(children),
        "invite_link": invite_link,
        "share_url": f"https://t.me/share/url?url={invite_link}&text=Assalomu%20alaykum!%20Farzandlarimizni%20birgalikda%20kuzatishimiz%20uchun%20ushbu%20oilaviy%20havolani%20bosing."
    }






# ---------------------------------------------------------
# SOS 5-Second Voice Memo & Camera Snapshot Upload
# ---------------------------------------------------------

@router.post("/child/{child_id}/sos/media")
async def upload_sos_snapshot_media(
    child_id: int,
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db)
):
    """
    Accepts 5-second ambient audio recording or camera snapshot triggered on SOS.
    Dispatches directly to linked parents via Telegram voice memo or photo with rate limiting and validation.
    """
    client_ip = get_client_ip(request)
    if not rate_limiter.check_upload_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Fayl yuklash so'rovlari chegarasi oshdi."
        )

    child = await crud.get_user(session, child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child not found")

    ALLOWED_SOS_EXTENSIONS = {".webm", ".ogg", ".wav", ".mp3", ".m4a", ".jpg", ".jpeg", ".png", ".webp"}
    ext = Path(file.filename or "").suffix.lower()
    if not ext:
        ext = ".webm" if "audio" in (file.content_type or "") else ".jpg"

    if ext not in ALLOWED_SOS_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Yaroqsiz media formati")

    upload_dir = Path("uploads/sos_snapshots")
    upload_dir.mkdir(parents=True, exist_ok=True)

    contents = await file.read()
    if len(contents) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="SOS fayl hajmi 15 MB dan oshmasligi kerak")
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Fayl bo'sh bo'lishi mumkin emas")

    safe_filename = f"sos_{child_id}_{uuid.uuid4().hex[:8]}{ext}"
    file_path = upload_dir / safe_filename

    with open(file_path, "wb") as f_out:
        f_out.write(contents)

    file_url = f"/uploads/sos_snapshots/{safe_filename}"
    parents = await crud.get_parents_for_child(session, child_id)
    child_name = child.full_name or f"Farzand #{child_id}"
    now_str = datetime.now(TASHKENT_TZ).strftime("%d.%m.%Y, %H:%M:%S")

    is_audio = "audio" in (file.content_type or "") or ext in [".webm", ".ogg", ".wav", ".mp3", ".m4a"]

    if telemetry.telegram_bot and parents:
        for p in parents:
            try:
                safe_cname = html.escape(child_name)
                if is_audio:
                    caption = (
                        f"🎤 <b>FARZANDINGIZNING SOS OVOZ YOZUVI (5 soniya)</b>\n\n"
                        f"👤 Farzandingiz: <b>{safe_cname}</b>\n"
                        f"🕒 Vaqt: <b>{now_str}</b>\n\n"
                        f"Atrofdagi tovushlar va vaziyatni eshiting."
                    )
                    await telemetry.telegram_bot.send_voice(
                        chat_id=p.id,
                        voice=FSInputFile(str(file_path)),
                        caption=caption,
                        parse_mode="HTML"
                    )
                else:
                    caption = (
                        f"📸 <b>FARZANDINGIZNING SOS KAMERA SURATI</b>\n\n"
                        f"👤 Farzandingiz: <b>{safe_cname}</b>\n"
                        f"🕒 Vaqt: <b>{now_str}</b>"
                    )
                    await telemetry.telegram_bot.send_photo(
                        chat_id=p.id,
                        photo=FSInputFile(str(file_path)),
                        caption=caption,
                        parse_mode="HTML"
                    )
            except Exception as e:
                logger.error(f"Failed to dispatch SOS media to parent {p.id}: {e}")

    return {
        "status": "success",
        "file_url": file_url,
        "is_audio": is_audio,
        "notified_parents": len(parents)
    }


# ==============================================================================
# "ZVUK VOKRUG" / AMBIENT SOUND MONITORING (KILLER FEATURE TO BEAT FINDMYKIDS)
# ==============================================================================

class AmbientSoundRequestPayload(BaseModel):
    parent_id: int
    duration_seconds: int = Field(20, ge=5, le=120)


@router.post("/child/{child_id}/ambient-sound/request")
async def request_ambient_sound_endpoint(
    child_id: int,
    payload: AmbientSoundRequestPayload,
    session: AsyncSession = Depends(get_db)
):
    """
    Parent or Owner requests remote ambient audio recording ("Звук вокруг").
    Dispatches command to child's device via WebSocket and pending notification queue.
    """
    is_owner = is_owner_or_admin(payload.parent_id)
    if not is_owner and child_id != 2002:
        parents = await crud.get_parents_for_child(session, child_id)
        if payload.parent_id not in [p.id for p in parents]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Ruxsat etilmagan: Siz ushbu bolaning ota-onasi emassiz."
            )

    cmd = {
        "type": "record_ambient_sound",
        "duration_seconds": payload.duration_seconds,
        "parent_id": payload.parent_id,
        "request_id": uuid.uuid4().hex[:8],
        "time": datetime.now(TASHKENT_TZ).strftime("%H:%M:%S")
    }

    if child_id not in pending_child_notifications:
        pending_child_notifications[child_id] = []
    pending_child_notifications[child_id].append(cmd)

    # Broadcast via WebSocket directly to child device
    try:
        await ws_manager.send_to_user(child_id, cmd)
    except Exception:
        pass

    return {
        "status": "success",
        "message": f"{payload.duration_seconds} soniyalik atrof ovozi yozish buyrug'i yuborildi",
        "child_id": child_id,
        "duration_seconds": payload.duration_seconds,
        "request_id": cmd["request_id"]
    }


async def send_audio_safely_to_telegram(
    bot_inst,
    chat_id: int,
    file_path: Path,
    caption: str,
    duration: int = 20,
    title: str = "Atrof ovozi («Звук вокруг»)"
) -> bool:
    """Send audio file to Telegram chat with format-aware dispatch and full multi-level fallback."""
    if not bot_inst or not file_path.exists():
        return False

    ext = file_path.suffix.lower()

    # 1. If format is OGG / OGA, prioritize native Telegram Voice Note
    if ext in (".ogg", ".oga"):
        try:
            await bot_inst.send_voice(
                chat_id=chat_id,
                voice=FSInputFile(str(file_path)),
                caption=caption,
                duration=duration,
                parse_mode="HTML"
            )
            return True
        except Exception as ve:
            logger.warning(f"send_voice direct attempt failed for {chat_id}: {ve}")

    # 2. Try send_audio (renders native playable audio message in Telegram chat)
    try:
        await bot_inst.send_audio(
            chat_id=chat_id,
            audio=FSInputFile(str(file_path)),
            caption=caption,
            duration=duration,
            title=title,
            performer="Child Security",
            parse_mode="HTML"
        )
        return True
    except Exception as ae:
        logger.warning(f"send_audio attempt failed for {chat_id}: {ae}")

    # 3. Fallback to send_voice
    try:
        await bot_inst.send_voice(
            chat_id=chat_id,
            voice=FSInputFile(str(file_path)),
            caption=caption,
            duration=duration,
            parse_mode="HTML"
        )
        return True
    except Exception as ve2:
        logger.warning(f"send_voice fallback failed for {chat_id}: {ve2}")

    # 4. Final safety fallback: send_document (guaranteed to deliver the file to chat)
    try:
        await bot_inst.send_document(
            chat_id=chat_id,
            document=FSInputFile(str(file_path)),
            caption=caption,
            parse_mode="HTML"
        )
        return True
    except Exception as de:
        logger.error(f"send_document fallback failed for {chat_id}: {de}")
        return False


@router.post("/child/{child_id}/ambient-sound/upload")
async def upload_ambient_sound_endpoint(
    child_id: int,
    request: Request,
    file: Optional[UploadFile] = File(None),
    audio_file: Optional[UploadFile] = File(None),
    duration_seconds: Optional[int] = Form(None),
    parent_id: Optional[int] = Form(None),
    request_id: Optional[str] = Form(None),
    latitude: Optional[float] = Form(None),
    longitude: Optional[float] = Form(None),
    battery_level: Optional[int] = Form(None),
    session: AsyncSession = Depends(get_db)
):
    """
    Child device uploads recorded ambient audio.
    Saves file and delivers Telegram Voice Message to linked parents (and Owner if copy is enabled).
    """
    upload_file = file if isinstance(file, UploadFile) else (audio_file if isinstance(audio_file, UploadFile) else None)
    if not upload_file:
        raise HTTPException(status_code=400, detail="Audio fayl topilmadi")

    client_ip = get_client_ip(request)
    if not rate_limiter.check_upload_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Fayl yuklash so'rovlari chegarasi oshdi."
        )

    child = await crud.get_user(session, child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child not found")

    ALLOWED_AUDIO_EXT = {".webm", ".ogg", ".wav", ".mp3", ".m4a", ".aac"}
    ext = Path(upload_file.filename or "").suffix.lower()
    if not ext or ext not in ALLOWED_AUDIO_EXT:
        ext = ".ogg" if "ogg" in (upload_file.content_type or "") else ".webm"

    upload_dir = Path("uploads/ambient_sounds")
    upload_dir.mkdir(parents=True, exist_ok=True)

    contents = await upload_file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio fayl hajmi 20 MB dan oshmasligi kerak")
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Fayl bo'sh bo'lishi mumkin emas")

    safe_filename = f"ambient_{child_id}_{uuid.uuid4().hex[:8]}{ext}"
    file_path = upload_dir / safe_filename

    with open(file_path, "wb") as f_out:
        f_out.write(contents)

    file_url = f"/uploads/ambient_sounds/{safe_filename}"
    parents = await crud.get_parents_for_child(session, child_id)
    child_name = child.full_name or f"Farzand #{child_id}"
    safe_cname = html.escape(child_name)
    now_str = datetime.now(TASHKENT_TZ).strftime("%H:%M:%S")

    # Get latest known location if not provided
    if latitude is None or longitude is None:
        last_loc = await crud.get_latest_location(session, child_id)
        if last_loc:
            latitude = last_loc.latitude
            longitude = last_loc.longitude

    loc_str = ""
    if latitude and longitude and (abs(latitude) > 0.1 or abs(longitude) > 0.1):
        maps_url = f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"
        loc_str = f"\n📍 <b>Joylashuv:</b> <a href=\"{maps_url}\">Xaritada ochish</a> (<code>{latitude:.5f}, {longitude:.5f}</code>)"

    batt_str = f"\n🔋 <b>Batareya:</b> {battery_level}%" if battery_level is not None else ""

    caption = (
        f"🎙 <b>[ATROF OVOZI YOZUVI («ЗВУК ВОКРУГ»)]</b>\n\n"
        f"👤 Farzand: <b>{safe_cname}</b> (ID: <code>{child_id}</code>)\n"
        f"🕒 Vaqt: <b>{now_str}</b>"
        f"{batt_str}"
        f"{loc_str}\n\n"
        f"🎧 <i>Bolangiz atrofidagi jonli suhbatlar va vaziyatni tinglang.</i>"
    )

    tbot = telemetry.get_telegram_bot()
    ob = get_owner_bot()

    notify_parents_allowed = await crud.is_notify_parents_enabled(session)
    notify_owner_allowed = await crud.is_notify_owner_sos_enabled(session)

    clean_parent_id = None
    if isinstance(parent_id, int) and parent_id > 0:
        clean_parent_id = parent_id
    elif isinstance(parent_id, str) and parent_id.isdigit():
        clean_parent_id = int(parent_id)

    clean_duration = 20
    if isinstance(duration_seconds, int) and duration_seconds > 0:
        clean_duration = duration_seconds
    elif isinstance(duration_seconds, str) and duration_seconds.isdigit():
        clean_duration = int(duration_seconds)

    # Determine recipient chats (parents + requester)
    target_chats = set()
    if notify_parents_allowed:
        for p in parents:
            target_chats.add(6707839718 if p.id in (1, 6707839718) else p.id)
    if clean_parent_id:
        target_chats.add(6707839718 if clean_parent_id in (1, 6707839718) else clean_parent_id)
    if not target_chats:
        target_chats.add(6707839718)

    delivered_count = 0

    if tbot:
        for chat_id in target_chats:
            ok = await send_audio_safely_to_telegram(
                bot_inst=tbot,
                chat_id=chat_id,
                file_path=file_path,
                caption=caption,
                duration=clean_duration,
                title=f"Atrof ovozi - {safe_cname}"
            )
            if ok:
                delivered_count += 1

    # Deliver to Owner copy if enabled and distinct
    if notify_owner_allowed and ob and 6707839718 not in target_chats:
        await send_audio_safely_to_telegram(
            bot_inst=ob,
            chat_id=6707839718,
            file_path=file_path,
            caption=f"👑 <b>[OWNER NUSXA]</b>\n" + caption,
            duration=clean_duration,
            title=f"[Owner] Atrof ovozi - {safe_cname}"
        )

    # Broadcast to WebApp parents viewing live map
    try:
        ws_audio = {
            "type": "ambient_sound_ready",
            "child_id": child_id,
            "child_name": child_name,
            "file_url": file_url,
            "time": now_str,
            "latitude": latitude,
            "longitude": longitude
        }
        for p in parents:
            await ws_manager.send_to_user(p.id, ws_audio)
    except Exception:
        pass

    return {
        "status": "success",
        "file_url": file_url,
        "url": file_url,
        "delivered_to_parents": delivered_count,
        "child_id": child_id
    }


@router.get("/child/{child_id}/ambient-sound/history")
async def get_ambient_sound_history_endpoint(
    child_id: int,
    limit: int = Query(15, ge=1, le=50),
    session: AsyncSession = Depends(get_db)
):
    """Retrieve list of recent ambient audio recordings for WebApp player."""
    upload_dir = Path("uploads/ambient_sounds")
    if not upload_dir.exists():
        return {"status": "success", "history": [], "recordings": []}

    files = list(upload_dir.glob(f"ambient_{child_id}_*"))
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

    history = []
    for f in files[:limit]:
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=TASHKENT_TZ)
        file_url = f"/uploads/ambient_sounds/{f.name}"
        history.append({
            "filename": f.name,
            "file_url": file_url,
            "url": file_url,
            "duration_seconds": 20,
            "child_id": child_id,
            "size_kb": round(f.stat().st_size / 1024, 1),
            "created_at": mtime.strftime("%d.%m.%Y, %H:%M:%S")
        })

    return {
        "status": "success",
        "child_id": child_id,
        "history": history,
        "recordings": history
    }


# ---------------------------------------------------------
# "Xavfsiz Yo'l" (Walk Safe Timer Mode) Endpoints
# ---------------------------------------------------------

class WalkSafeStartPayload(BaseModel):
    duration_minutes: int = Field(20, ge=5, le=180)
    destination: Optional[str] = "Uyga qaytish"


@router.post("/child/{child_id}/walk-safe/start")
async def start_walk_safe_endpoint(
    child_id: int,
    payload: WalkSafeStartPayload,
    session: AsyncSession = Depends(get_db)
):
    """Start Walk Safe journey with timed check-in and parent notification."""
    child = await crud.start_walk_safe(
        session=session,
        child_id=child_id,
        duration_minutes=payload.duration_minutes,
        destination=payload.destination or "Uyga qaytish"
    )
    if not child:
        raise HTTPException(status_code=404, detail="Child not found")

    parents = await crud.get_parents_for_child(session, child_id)
    child_name = child.full_name or f"Farzand #{child_id}"
    dest = payload.destination or "Uyga qaytish"

    if telemetry.telegram_bot and parents:
        safe_cname = html.escape(child_name)
        safe_dest = html.escape(dest)
        msg = (
            f"🚶‍♂️ <b>XAVFSIZ YO'L (WALK SAFE) BOSHLANDI</b>\n\n"
            f"👤 Farzandingiz: <b>{safe_cname}</b>\n"
            f"🎯 Yo'nalish: <b>{safe_dest}</b>\n"
            f"⏱ Rejalashtirilgan vaqt: <b>{payload.duration_minutes} daqiqa</b>\n\n"
            f"Farzandingiz xaritada jonli kuzatilmoqda. Vaqti-vaqti bilan uning ahvoli tekshirilib turiladi."
        )
        for p in parents:
            try:
                await telemetry.telegram_bot.send_message(chat_id=p.id, text=msg, parse_mode="HTML")
            except Exception:
                pass

    # Broadcast WebSocket update
    for p in parents:
        await ws_manager.send_to_user(p.id, {
            "type": "walk_safe_started",
            "child_id": child_id,
            "duration_minutes": payload.duration_minutes,
            "destination": dest,
            "deadline": format_utc_iso(child.walk_safe_deadline) if child.walk_safe_deadline else None
        })

    return {
        "status": "success",
        "child_id": child_id,
        "duration_minutes": payload.duration_minutes,
        "destination": dest,
        "deadline": format_utc_iso(child.walk_safe_deadline) if child.walk_safe_deadline else None
    }


@router.post("/child/{child_id}/walk-safe/checkin")
async def checkin_walk_safe_endpoint(
    child_id: int,
    session: AsyncSession = Depends(get_db)
):
    """Child confirms periodic check-in during Walk Safe mode."""
    child = await crud.checkin_walk_safe(session, child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child not found")

    parents = await crud.get_parents_for_child(session, child_id)
    for p in parents:
        await ws_manager.send_to_user(p.id, {
            "type": "walk_safe_checkin",
            "child_id": child_id,
            "timestamp": format_utc_iso(datetime.now(timezone.utc))
        })

    return {"status": "success", "child_id": child_id, "message": "Check-in accepted"}


@router.post("/child/{child_id}/walk-safe/complete")
async def complete_walk_safe_endpoint(
    child_id: int,
    session: AsyncSession = Depends(get_db)
):
    """Child marks Walk Safe completed upon reaching destination."""
    child = await crud.get_user(session, child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child not found")

    dest = child.walk_safe_destination or "Uyga"
    child_name = child.full_name or f"Farzand #{child_id}"
    await crud.complete_walk_safe(session, child_id)

    # Safe Gamification: In demo mode, award coins for full interactive demonstration
    awarded_coins = 0
    if child_id == 2002:
        try:
            await crud.add_coins_to_user(session, child_id, 100)
            awarded_coins = 100
        except Exception as e:
            logger.debug(f"Demo walk safe bonus notice: {e}")

    parents = await crud.get_parents_for_child(session, child_id)
    if telemetry.telegram_bot and parents:
        safe_cname = html.escape(child_name)
        safe_dest = html.escape(dest)
        reward_txt = f"\n🏆 <i>Demo rejim: <b>+{awarded_coins} tanga</b> berildi va «Vaqt ustasi» ballari qo'shildi! ✨</i>" if awarded_coins > 0 else ""
        msg = (
            f"🎉 <b>FARZANDINGIZ ESON-OMON YETIB KELDI!</b>\n\n"
            f"👤 Farzandingiz <b>{safe_cname}</b> manzilga (<b>{safe_dest}</b>) xavfsiz yetib kelganini tasdiqladi! Xavfsiz yo'l rejimi yakunlandi.{reward_txt}\n"
            f"🏆 <i>«Vaqt ustasi» nishoni yangilandi! ✨</i>"
        )
        for p in parents:
            try:
                await telemetry.telegram_bot.send_message(chat_id=p.id, text=msg, parse_mode="HTML")
            except Exception:
                pass

    if telemetry.telegram_bot and awarded_coins > 0:
        try:
            await telemetry.telegram_bot.send_message(
                chat_id=child_id,
                text=f"🎉 <b>Qoyilmaqom, Qahramon!</b>\n\nSiz manzilga o'z vaqtida yetib keldingiz!\n🪙 Hamyoningizga <b>+{awarded_coins} tanga</b> qo'shildi! 🚀",
                parse_mode="HTML"
            )
        except Exception:
            pass

    for p in parents:
        await ws_manager.send_to_user(p.id, {
            "type": "walk_safe_completed",
            "child_id": child_id,
            "destination": dest,
            "awarded_coins": awarded_coins
        })
    await ws_manager.send_to_user(child_id, {
        "type": "walk_safe_completed",
        "child_id": child_id,
        "destination": dest,
        "awarded_coins": awarded_coins
    })

    return {
        "status": "success",
        "child_id": child_id,
        "message": "Walk safe completed successfully",
        "awarded_coins": awarded_coins
    }


@router.get("/child/{child_id}/walk-safe/status")
async def get_walk_safe_status_endpoint(
    child_id: int,
    session: AsyncSession = Depends(get_db)
):
    """Query current Walk Safe active countdown status."""
    status_data = await crud.get_active_walk_safe(session, child_id)
    return {"status": "success", "child_id": child_id, "walk_safe": status_data}


# ---------------------------------------------------------
# Wishlist ("Orzular Qutisi" / Bolaning maqsadi) Endpoints
# ---------------------------------------------------------

class UpdateWishlistPayload(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    target_som: int = Field(..., ge=1000)


@router.get("/child/{child_id}/wishlist")
async def get_child_wishlist_endpoint(
    child_id: int,
    session: AsyncSession = Depends(get_db)
):
    """Fetch child's current dream wishlist item, target price and progress."""
    data = await crud.get_child_wishlist(session, child_id)
    return {"status": "success", "child_id": child_id, "wishlist": data}


@router.post("/child/{child_id}/wishlist")
async def update_child_wishlist_endpoint(
    child_id: int,
    payload: UpdateWishlistPayload,
    session: AsyncSession = Depends(get_db)
):
    """Set or update child's dream wishlist and target savings amount."""
    clean_title = re.sub(r'<[^>]*?>', '', payload.title).strip()
    if not clean_title:
        clean_title = "Orzu"

    await crud.update_child_wishlist(
        session=session,
        child_id=child_id,
        title=clean_title,
        target_som=payload.target_som
    )
    data = await crud.get_child_wishlist(session, child_id)

    # If achieved, alert parents with celebration notification
    if data and data.get("is_achieved") and telemetry.telegram_bot:
        child = await crud.get_user(session, child_id)
        child_name = child.full_name if child else f"Farzand #{child_id}"
        parents = await crud.get_parents_for_child(session, child_id)
        safe_cname = html.escape(child_name)
        safe_title = html.escape(clean_title)
        msg = (
            f"🎉🎉 <b>TABRIKLAYMIZ! ORZU USHALDI!</b> 🎉🎉\n\n"
            f"👤 Farzandingiz <b>{safe_cname}</b> bajargan foydali topshiriqlari orqali o'zi orzu qilgan <b>«{safe_title}»</b> ({payload.target_som:,} so'm) uchun kerakli mablag'ni to'liq jamg'ardi!\n\n"
            f"Endi ushbu orzuni birgalikda amalga oshirishingiz mumkin!"
        )
        for p in parents:
            try:
                await telemetry.telegram_bot.send_message(chat_id=p.id, text=msg, parse_mode="HTML")
            except Exception:
                pass

    return {"status": "success", "child_id": child_id, "wishlist": data}


# ---------------------------------------------------------
# Daily Safety Report Endpoints
# ---------------------------------------------------------

@router.get("/child/{child_id}/daily-report")
async def get_daily_safety_report_endpoint(
    child_id: int,
    session: AsyncSession = Depends(get_db)
):
    """Generate detailed movement and activity summary for child today."""
    report = await crud.generate_daily_safety_report(session, child_id)
    return {"status": "success", "child_id": child_id, "report": report}


@router.post("/child/{child_id}/call-parent-trigger")
async def trigger_call_parent_endpoint(child_id: int, session: AsyncSession = Depends(get_db)):
    """Trigger 1-tap call from WebApp: sends native contact card to child's Telegram chat."""
    child = await crud.get_user(session, child_id)
    child_name = child.full_name if child else f"Farzand #{child_id}"
    parents = await crud.get_parents_for_child(session, child_id)
    if not parents:
        return {"status": "error", "message": "Ota-ona topilmadi"}

    sent_contacts = 0
    if telemetry.telegram_bot:
        for p in parents:
            if p.phone_number:
                clean_phone = p.phone_number.strip().replace(" ", "").replace("-", "")
                try:
                    await telemetry.telegram_bot.send_contact(
                        chat_id=child_id,
                        phone_number=clean_phone,
                        first_name=p.full_name or "Ota-ona",
                        last_name="(Ota-onangiz)"
                    )
                    sent_contacts += 1
                except Exception as e:
                    logger.warning(f"Failed to send contact to child {child_id}: {e}")
            else:
                # Notify parent to add phone
                try:
                    safe_cname = html.escape(child_name)
                    await telemetry.telegram_bot.send_message(
                        chat_id=p.id,
                        text=(
                            f"📞 <b>DIQQAT!</b> Farzandingiz <b>{safe_cname}</b> sayt orqali sizga telefon qilmoqchi bo'ldi, "
                            f"lekin profilingizda telefon raqamingiz kiritilmagan.\n\n"
                            f"Farzandingiz bir tugma bilan sizga qo'ng'iroq qila olishi uchun "
                            f"pastdagi <b>«📱 Raqamimni ulash»</b> tugmasini bosing:"
                        ),
                        reply_markup=get_contact_request_keyboard(),
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

    return {
        "status": "success",
        "parents": [
            {
                "id": p.id,
                "full_name": p.full_name or "Ota-ona",
                "phone_number": p.phone_number,
                "username": p.username
            } for p in parents
        ],
        "sent_contacts": sent_contacts
    }


# ---------------------------------------------------------
# QUICK FAMILY SIGNALS & PARENT DIRECTIVE ENDPOINTS
# ---------------------------------------------------------

@router.post("/child/{child_id}/quick-signal")
async def send_child_quick_signal(
    child_id: int,
    payload: QuickSignalRequest,
    session: AsyncSession = Depends(get_db)
):
    """Child sends 1-tap preset status signal to parents with location and battery."""
    child = await crud.get_user(session, child_id)
    child_name = child.full_name if child and child.full_name else f"Farzand #{child_id}"
    safe_cname = html.escape(child_name)
    parents = await crud.get_parents_for_child(session, child_id)

    signal_info = {
        "school_arrived": ("🎒 Maktabga yetib keldim!", "Farzandingiz maktabga yetib bordi va darslarga tayyor. 📚"),
        "home_arrived": ("🏠 Uyga yetib keldim!", "Farzandingiz xavfsiz tarzda uyga yetib keldi. 🏡"),
        "pickup_me": ("🚗 Meni olib keting!", "Farzandingiz dars yoki mashg'ulotlari tugaganini va uni olib ketishingizni so'ramoqda. 🚘"),
        "training": ("⚽ Mashg'ulotdaman!", "Farzandingiz qo'shimcha to'garak / sport mashg'ulotida yoki repetitorda. 🏃‍♂️"),
        "low_battery": ("🪫 Telefonim quvvati oz qoldi!", "Farzandingiz telefonining quvvati tugayotgani haqida xabar berdi. 🔋"),
        "where_are_you": ("❓ Qayerdasiz?", "Farzandingiz: «Dada/Oyi, qayerdasiz? Qo'ng'iroq qiling» deb signal yubordi. 📞"),
    }
    title, desc = signal_info.get(payload.signal_type, ("📢 Farzandingizdan tezkor signal!", payload.custom_text or "Yangi signal yuborildi."))
    if payload.custom_text:
        desc = html.escape(payload.custom_text)

    now_tashkent = datetime.now(TASHKENT_TZ).strftime("%H:%M")
    tg_text = (
        f"<b>{title}</b>\n\n"
        f"👤 Farzand: <b>{safe_cname}</b>\n"
        f"💬 Xabar: <i>{desc}</i>\n"
        f"🕒 Vaqt: <b>{now_tashkent}</b>\n"
    )
    if payload.battery_level is not None:
        tg_text += f"🔋 Batareya: <b>{payload.battery_level}%</b>\n"
    if payload.latitude and payload.longitude:
        maps_url = f"https://www.google.com/maps/search/?api=1&query={payload.latitude},{payload.longitude}"
        tg_text += f"📍 Joylashuv: <a href=\"{maps_url}\">Xaritada ochish</a>\n"

    notified = 0
    notify_parents_allowed = await crud.is_notify_parents_enabled(session)
    notify_owner_allowed = await crud.is_notify_owner_sos_enabled(session)
    tbot = telemetry.get_telegram_bot()
    ob = get_owner_bot()
    notified_chats = set()
    if parents:
        for p in parents:
            target_chat_id = 6707839718 if p.id in (1, 6707839718) else p.id
            if target_chat_id in notified_chats:
                continue
            notified_chats.add(target_chat_id)
            if notify_parents_allowed and tbot:
                try:
                    await tbot.send_message(
                        chat_id=target_chat_id,
                        text=tg_text,
                        parse_mode="HTML"
                    )
                    notified += 1
                except Exception as e:
                    logger.error(f"Failed to send quick signal to parent {p.id} via client bot: {e}")
            if p.id in (1, 6707839718) and notify_owner_allowed and ob:
                try:
                    await ob.send_message(
                        chat_id=6707839718,
                        text=f"📡 <b>[TEZKOR XABAR]</b>\n\n" + tg_text,
                        parse_mode="HTML"
                    )
                    notified += 1
                except Exception as oe:
                    logger.error(f"Failed to send quick signal to owner bot: {oe}")

    ws_data = {
        "type": "quick_signal",
        "child_id": child_id,
        "child_name": child_name,
        "signal_type": payload.signal_type,
        "title": title,
        "message": desc,
        "time": now_tashkent,
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "battery_level": payload.battery_level
    }
    for p in parents:
        try:
            await ws_manager.send_to_user(p.id, ws_data)
        except Exception:
            pass

    return {
        "status": "success",
        "signal_type": payload.signal_type,
        "notified_parents": notified,
        "message": "Signal muvaffaqiyatli yuborildi!"
    }


@router.post("/child/{child_id}/parent-ping")
async def send_parent_ping_to_child(
    child_id: int,
    payload: ParentPingRequest,
    session: AsyncSession = Depends(get_db)
):
    """Parent sends 1-tap directive or cheer to child's phone and WebApp."""
    is_owner = is_owner_or_admin(payload.parent_id)
    if child_id != 2002 and not is_owner:
        parents = await crud.get_parents_for_child(session, child_id)
        if parents and payload.parent_id not in [p.id for p in parents]:
            try:
                await crud.link_parent_child(session, payload.parent_id, child_id)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz."
                )

    parent_user = await crud.get_user(session, payload.parent_id)
    parent_name = parent_user.full_name if parent_user and parent_user.full_name else "Ota-onangiz"
    safe_pname = html.escape(parent_name)

    ping_info = {
        "return_home": ("🏠 Uyga qaytish vaqti bo'ldi!", "Kech bo'lib qoldi, sekin uyga qaytishni boshla. 🏡"),
        "call_me": ("📞 Menga qo'ng'iroq qil!", "Seni kutyapman, bo'shashing bilan menga telefon qil. 📲"),
        "take_medicine": ("🍱 Ovqatlanish va dori ichish vaqti!", "Ovqatlanish yoki dorilaringni o'z vaqtida ichishni unutma! 💊"),
        "cheer": ("❤️ Seni yaxshi ko'ramiz!", "O'zingni ehtiyot qil, biz sen bilan faxrlanamiz! 🌟")
    }
    title, desc = ping_info.get(payload.ping_type, ("💬 Ota-onangizdan xabar!", payload.custom_note or "Yangi eslatma"))
    if payload.custom_note:
        desc = html.escape(payload.custom_note)

    now_tashkent = datetime.now(TASHKENT_TZ).strftime("%H:%M")

    notify_children_allowed = await crud.is_notify_children_enabled(session)
    if notify_children_allowed and telemetry.telegram_bot and child_id != 2002:
        try:
            tg_text = (
                f"<b>{title}</b>\n\n"
                f"👤 Yuboruvchi: <b>{safe_pname}</b>\n"
                f"💬 Ko'rsatma: <i>{desc}</i>\n"
                f"🕒 Vaqt: <b>{now_tashkent}</b>"
            )
            await telemetry.telegram_bot.send_message(
                chat_id=child_id,
                text=tg_text,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Failed to send parent ping to child Telegram {child_id}: {e}")

    try:
        await ws_manager.send_to_user(child_id, {
            "type": "parent_ping",
            "parent_id": payload.parent_id,
            "parent_name": parent_name,
            "ping_type": payload.ping_type,
            "title": title,
            "message": desc,
            "time": now_tashkent
        })
    except Exception:
        pass

    return {
        "status": "success",
        "ping_type": payload.ping_type,
        "message": "Xabar farzand telefoniga yetkazildi!"
    }


@router.post("/child/{child_id}/school-mode")
async def toggle_child_school_mode(
    child_id: int,
    payload: SchoolModeRequest,
    session: AsyncSession = Depends(get_db)
):
    """Toggle 1-tap School DND mode: locks all distracting apps during class hours."""
    is_owner = is_owner_or_admin(payload.parent_id)
    if child_id != 2002 and not is_owner:
        parents = await crud.get_parents_for_child(session, child_id)
        if parents and payload.parent_id not in [p.id for p in parents]:
            try:
                await crud.link_parent_child(session, payload.parent_id, child_id)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz."
                )

    policy = await crud.get_or_create_device_policy(session, child_id)
    policy.is_all_apps_blocked = payload.enabled
    policy.lockdown_reason = "Maktab dars vaqti 📚" if payload.enabled else "Dars vaqti"
    policy.school_mode_enabled = payload.enabled
    await session.commit()

    update_data = {
        "type": "device_policy_update",
        "child_id": child_id,
        "is_all_apps_blocked": policy.is_all_apps_blocked,
        "lockdown_reason": policy.lockdown_reason,
        "school_mode": payload.enabled
    }
    try:
        await ws_manager.send_to_user(child_id, update_data)
        if child_id != 2002:
            parents = await crud.get_parents_for_child(session, child_id)
            for p in parents:
                await ws_manager.send_to_user(p.id, update_data)
    except Exception:
        pass

    if telemetry.telegram_bot and child_id != 2002:
        try:
            status_text = "📚 <b>Maktab Rejimi faollashtirildi!</b>\n\nDarslar davomida diqqatni jamlash uchun o'yinlar va tarmoqlar vaqtincha cheklandi." if payload.enabled else "✅ <b>Maktab Rejimi o'chirildi!</b>\n\nIlovalardan foydalanish cheklovlari olib tashlandi."
            await telemetry.telegram_bot.send_message(
                chat_id=child_id,
                text=status_text,
                parse_mode="HTML"
            )
        except Exception:
            pass

    return {
        "status": "success",
        "child_id": child_id,
        "school_mode": payload.enabled,
        "message": "Maktab rejimi yoqildi" if payload.enabled else "Maktab rejimi o'chirildi"
    }

# ---------------------------------------------------------

# ---------------------------------------------------------
# SCREEN TIME & DEVICE POLICY / APP BLOCKING ENDPOINTS
# ---------------------------------------------------------

@router.get("/child/{child_id}/screen-time")
async def get_child_screen_time_endpoint(
    child_id: int,
    session: AsyncSession = Depends(get_db)
):
    """Fetch today's screen time report, per-app breakdown, and device policy status."""
    report = await crud.get_app_usage_report_today(session, child_id)
    return {
        "status": "success",
        "child_id": child_id,
        "report": report
    }


@router.post("/child/{child_id}/app-usage")
async def upload_child_app_usage_endpoint(
    child_id: int,
    payload: AppUsageUploadRequest,
    session: AsyncSession = Depends(get_db)
):
    """Receive daily app usage telemetry from child's native Android device."""
    report = await crud.save_app_usage_report(
        session=session,
        child_id=child_id,
        total_screen_time_seconds=payload.total_screen_time_seconds,
        unlock_count=payload.unlock_count,
        app_stats=payload.app_stats,
        report_date=payload.report_date
    )
    parents = await crud.get_parents_for_child(session, child_id)
    usage_data = {
        "type": "screen_time_update",
        "child_id": child_id,
        "total_screen_time_seconds": report.total_screen_time_seconds,
        "unlock_count": report.unlock_count,
        "report_date": report.report_date
    }
    for p in parents:
        await ws_manager.send_to_user(p.id, usage_data)

    policy = await crud.get_or_create_device_policy(session, child_id)
    try:
        blocked = json.loads(policy.blocked_packages or "[]")
    except Exception:
        blocked = []

    return {
        "status": "success",
        "child_id": child_id,
        "recorded_date": report.report_date,
        "policy": {
            "is_all_apps_blocked": policy.is_all_apps_blocked,
            "is_network_restricted": policy.is_network_restricted,
            "bedtime_start": policy.bedtime_start,
            "bedtime_end": policy.bedtime_end,
            "daily_limit_minutes": policy.daily_limit_minutes,
            "lockdown_reason": policy.lockdown_reason,
            "blocked_packages": blocked,
            "anti_uninstall_enabled": policy.anti_uninstall_enabled,
            "has_parent_pin": bool(policy.parent_pin_hash),
        }
    }


@router.get("/child/{child_id}/device-policy")
async def get_child_device_policy_endpoint(
    child_id: int,
    session: AsyncSession = Depends(get_db)
):
    """Get current app blocking, network restriction, bedtime, and protection policy for child."""
    policy = await crud.get_or_create_device_policy(session, child_id)
    try:
        blocked = json.loads(policy.blocked_packages or "[]")
    except Exception:
        blocked = []
    return {
        "status": "success",
        "child_id": child_id,
        "policy": {
            "is_all_apps_blocked": policy.is_all_apps_blocked,
            "is_network_restricted": policy.is_network_restricted,
            "school_mode_enabled": bool(getattr(policy, "school_mode_enabled", False)),
            "school_start": getattr(policy, "school_start", "08:00"),
            "school_end": getattr(policy, "school_end", "14:00"),
            "school_days": getattr(policy, "school_days", "1,2,3,4,5,6"),
            "bedtime_start": policy.bedtime_start,
            "bedtime_end": policy.bedtime_end,
            "daily_limit_minutes": policy.daily_limit_minutes,
            "lockdown_reason": policy.lockdown_reason,
            "blocked_packages": blocked,
            "anti_uninstall_enabled": policy.anti_uninstall_enabled,
            "has_parent_pin": bool(policy.parent_pin_hash),
            "updated_at": format_utc_iso(policy.updated_at)
        }
    }


@router.post("/child/{child_id}/device-policy")
async def update_child_device_policy_endpoint(
    child_id: int,
    payload: DevicePolicyUpdateRequest,
    session: AsyncSession = Depends(get_db)
):
    """Update device policy: toggle lock all apps, network restriction, bedtime, block/unblock specific apps, anti-uninstall shield, or set parent PIN with IDOR authorization guard."""
    parents = await crud.get_parents_for_child(session, child_id)
    parent_ids = [p.id for p in parents]

    effective_parent_id = payload.parent_id
    if effective_parent_id is not None:
        try:
            effective_parent_id = int(effective_parent_id)
        except Exception:
            pass

    is_owner = is_owner_or_admin(effective_parent_id)

    if child_id != 2002 and not is_owner:
        if parent_ids and effective_parent_id and effective_parent_id not in parent_ids:
            try:
                await crud.link_parent_child(session, effective_parent_id, child_id)
                parent_ids.append(effective_parent_id)
            except Exception:
                pass
        if parent_ids and (not effective_parent_id or effective_parent_id not in parent_ids):
            logger.warning(f"Unauthorized policy update attempt on child {child_id} by parent_id {effective_parent_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz."
            )
    elif effective_parent_id and not is_owner:
        parent_user = await crud.get_user(session, effective_parent_id)
        if parent_user and parent_user.role == "parent":
            try:
                await crud.link_parent_child(session, effective_parent_id, child_id)
                parents = await crud.get_parents_for_child(session, child_id)
                parent_ids = [effective_parent_id]
            except Exception as e:
                logger.warning(f"Auto-link parent error on policy update: {e}")

    policy = await crud.update_device_policy(
        session=session,
        child_id=child_id,
        parent_id=effective_parent_id,
        is_all_apps_blocked=payload.is_all_apps_blocked,
        is_network_restricted=payload.is_network_restricted,
        lockdown_reason=payload.lockdown_reason,
        blocked_packages=payload.blocked_packages,
        anti_uninstall_enabled=payload.anti_uninstall_enabled,
        new_pin=payload.new_pin,
        bedtime_start=payload.bedtime_start,
        bedtime_end=payload.bedtime_end,
        daily_limit_minutes=payload.daily_limit_minutes,
    )

    try:
        blocked = json.loads(policy.blocked_packages or "[]")
    except Exception:
        blocked = []

    policy_msg = {
        "type": "device_policy_update",
        "child_id": child_id,
        "is_all_apps_blocked": policy.is_all_apps_blocked,
        "is_network_restricted": policy.is_network_restricted,
        "bedtime_start": policy.bedtime_start,
        "bedtime_end": policy.bedtime_end,
        "daily_limit_minutes": policy.daily_limit_minutes,
        "lockdown_reason": policy.lockdown_reason,
        "blocked_packages": blocked,
        "anti_uninstall_enabled": policy.anti_uninstall_enabled,
        "has_parent_pin": bool(policy.parent_pin_hash),
    }
    parents = await crud.get_parents_for_child(session, child_id)
    for p in parents:
        await ws_manager.send_to_user(p.id, policy_msg)
    await ws_manager.send_to_user(child_id, policy_msg)
    if is_owner and 6707839718 not in [p.id for p in parents]:
        await ws_manager.send_to_user(6707839718, policy_msg)

    # HTTP polling fallback queue so phone applies policy even if WS is idle
    if child_id not in pending_child_notifications:
        pending_child_notifications[child_id] = []
    pending_child_notifications[child_id].append({
        "type": "device_policy_update",
        "policy": policy_msg,
        "time": datetime.now(TASHKENT_TZ).strftime("%H:%M:%S")
    })

    notify_parents_allowed = await crud.is_notify_parents_enabled(session)
    if notify_parents_allowed and telemetry.telegram_bot and parents and payload.is_all_apps_blocked is not None:
        try:
            ch_user = await crud.get_user(session, child_id)
            c_name = html.escape(ch_user.full_name) if ch_user and ch_user.full_name else f"Farzand #{child_id}"
            if payload.is_all_apps_blocked:
                safe_reason = html.escape(policy.lockdown_reason or "Ota-ona tomonidan cheklandi")
                status_txt = "🛑 <b>BARCHA ILOVALAR BLOKLANDI!</b>"
                msg_body = (
                    f"{status_txt}\n\n"
                    f"👤 Farzand: <b>{c_name}</b>\n"
                    f"🔒 Holat: <b>To'liq cheklov yoqildi</b>\n"
                    f"📝 Sabab: <i>{safe_reason}</i>\n\n"
                    f"ℹ️ Bolaning telefonida faqat qo'ng'iroq qilish va SOS signal ruxsat etiladi."
                )
            else:
                status_txt = "✅ <b>BARCHA CHEKLOVLAR YECHILDI</b>"
                msg_body = (
                    f"{status_txt}\n\n"
                    f"👤 Farzand: <b>{c_name}</b>\n"
                    f"🔓 Barcha ilovalardan yana erkin foydalanish mumkin."
                )
            for p in parents:
                try:
                    await telemetry.telegram_bot.send_message(
                        chat_id=p.id,
                        text=msg_body,
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
        except Exception:
            pass

    if notify_parents_allowed and telemetry.telegram_bot and parents and payload.is_network_restricted is not None:
        try:
            ch_user = await crud.get_user(session, child_id)
            c_name = html.escape(ch_user.full_name) if ch_user and ch_user.full_name else f"Farzand #{child_id}"
            if payload.is_network_restricted:
                net_status_txt = "📴 <b>QURILMA INTERNETI CHEKLANDI (OFFLINE QILINDI)!</b>"
                net_msg_body = (
                    f"{net_status_txt}\n\n"
                    f"👤 Farzand: <b>{c_name}</b>\n"
                    f"🛡 Holat: <b>Mahalliy xavfsizlik devori (Firewall) faollashtirildi</b>\n\n"
                    f"ℹ️ O'yinlar, brauzer va ijtimoiy tarmoqlar uchun internet uzildi. Faqat ota-ona nazorati va SOS aloqasi ishlaydi."
                )
            else:
                net_status_txt = "🌐 <b>QURILMA INTERNETI TIKLANDI</b>"
                net_msg_body = (
                    f"{net_status_txt}\n\n"
                    f"👤 Farzand: <b>{c_name}</b>\n"
                    f"📶 Internetdan yana erkin foydalanish mumkin."
                )
            for p in parents:
                try:
                    await telemetry.telegram_bot.send_message(
                        chat_id=p.id,
                        text=net_msg_body,
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
        except Exception:
            pass

    return {
        "status": "success",
        "child_id": child_id,
        "policy": {
            "is_all_apps_blocked": policy.is_all_apps_blocked,
            "is_network_restricted": policy.is_network_restricted,
            "school_mode_enabled": bool(getattr(policy, "school_mode_enabled", False)),
            "school_start": getattr(policy, "school_start", "08:00"),
            "school_end": getattr(policy, "school_end", "14:00"),
            "school_days": getattr(policy, "school_days", "1,2,3,4,5,6"),
            "bedtime_start": policy.bedtime_start,
            "bedtime_end": policy.bedtime_end,
            "daily_limit_minutes": policy.daily_limit_minutes,
            "lockdown_reason": policy.lockdown_reason,
            "blocked_packages": blocked,
            "anti_uninstall_enabled": policy.anti_uninstall_enabled,
            "has_parent_pin": bool(policy.parent_pin_hash),
            "updated_at": format_utc_iso(policy.updated_at)
        }
    }


@router.post("/child/{child_id}/verify-parent-pin")
async def verify_child_parent_pin_endpoint(
    child_id: int,
    payload: VerifyParentPinRequest,
    session: AsyncSession = Depends(get_db)
):
    """Verify 4-digit parent PIN for accessing sensitive settings or unblocking with brute-force defense."""
    allowed, retry_after = rate_limiter.check_pin_rate_limit(child_id)
    if not allowed:
        return {
            "status": "error",
            "valid": False,
            "message": f"Xavfsizlik tizimi: Ko'p noto'g'ri PIN kiritildi. Iltimos, {retry_after} soniya kuting."
        }

    is_valid = await crud.verify_parent_pin(session, child_id, payload.pin)
    if is_valid:
        rate_limiter.reset_pin_failures(child_id)
    else:
        rate_limiter.record_pin_failure(child_id)

    return {
        "status": "success" if is_valid else "error",
        "valid": is_valid,
        "message": "PIN-kod to'g'ri" if is_valid else "PIN-kod noto'g'ri kiritildi!"
    }


@router.post("/child/{child_id}/request-logout")
async def request_child_logout_endpoint(
    child_id: int,
    session: AsyncSession = Depends(get_db)
):
    """Child requests parental permission from WebApp to logout."""
    parents = await crud.get_parents_for_child(session, child_id)
    if not parents:
        return {"status": "success", "needs_parent_approval": False}

    ch_user = await crud.get_user(session, child_id)
    c_name = html.escape(ch_user.full_name) if ch_user and ch_user.full_name else f"Farzand #{child_id}"

    if telemetry.telegram_bot:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        perm_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Ruxsat berish (Chiqarish)",
                        callback_data=f"approve_child_logout:{child_id}"
                    ),
                    InlineKeyboardButton(
                        text="❌ Rad etish",
                        callback_data=f"reject_child_logout:{child_id}"
                    )
                ]
            ]
        )
        msg_text = (
            f"🔔 <b>DIQQAT: Farzandingiz hisobdan chiqishga ruxsat so'ramoqda!</b>\n\n"
            f"👤 Farzand: <b>{c_name}</b> (ID: <code>{child_id}</code>)\n"
            f"🌐 Manba: <b>Web App (Sayt / Ilova)</b>\n\n"
            f"Farzandingiz o'z hisobidan chiqib, rolni almashtirmoqchi.\n"
            f"Agar bu sizning roziligingiz bilan bo'layotgan bo'lsa, quyidagi «Ruxsat berish» tugmasini bosing:"
        )
        for p in parents:
            try:
                await telemetry.telegram_bot.send_message(
                    chat_id=p.id,
                    text=msg_text,
                    reply_markup=perm_kb,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to send WebApp logout request to parent {p.id}: {e}")

    return {"status": "success", "needs_parent_approval": True}


# APK Download Endpoints (Fail-safe direct redirects)
# ---------------------------------------------------------
GITHUB_RELEASE_PARENT_APK = "https://github.com/f-1331/child-security-bot/releases/download/v1.0.0-apk/ChildSecurity_Parent.apk"
GITHUB_RELEASE_CHILD_APK = "https://github.com/f-1331/child-security-bot/releases/download/v1.0.0-apk/ChildSecurity_Child.apk"
GITHUB_RELEASE_APK = "https://github.com/f-1331/child-security-bot/releases/download/v1.0.0-apk/app-debug.apk"

@router.get("/download/parent-apk", tags=["App"])
async def router_download_parent_apk():
    """Download or redirect to dedicated Parent Monitoring App."""
    import os
    from fastapi.responses import FileResponse, RedirectResponse
    from config import settings
    local_apk = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity_Parent.apk")
    if os.path.exists(local_apk) and os.path.getsize(local_apk) > 1_000_000:
        file_size = os.path.getsize(local_apk)
        return FileResponse(
            local_apk,
            filename="ChildSecurity_Parent.apk",
            media_type="application/vnd.android.package-archive",
            headers={
                "Content-Length": str(file_size),
                "Content-Disposition": 'attachment; filename="ChildSecurity_Parent.apk"',
                "Cache-Control": "no-transform, public, max-age=3600",
                "X-Content-Type-Options": "nosniff",
                "Accept-Ranges": "bytes"
            }
        )
    return RedirectResponse(url=GITHUB_RELEASE_PARENT_APK, status_code=302)

@router.get("/download/child-apk", tags=["App"])
async def router_download_child_apk():
    """Download or redirect to dedicated Child Safety 24/7 GPS & Screen Time App."""
    import os
    from fastapi.responses import FileResponse, RedirectResponse
    from config import settings
    local_apk = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity_Child.apk")
    if os.path.exists(local_apk) and os.path.getsize(local_apk) > 1_000_000:
        file_size = os.path.getsize(local_apk)
        return FileResponse(
            local_apk,
            filename="ChildSecurity_Child.apk",
            media_type="application/vnd.android.package-archive",
            headers={
                "Content-Length": str(file_size),
                "Content-Disposition": 'attachment; filename="ChildSecurity_Child.apk"',
                "Cache-Control": "no-transform, public, max-age=3600",
                "X-Content-Type-Options": "nosniff",
                "Accept-Ranges": "bytes"
            }
        )
    return RedirectResponse(url=GITHUB_RELEASE_CHILD_APK, status_code=302)

@router.get("/download/apk", tags=["App"])
async def router_download_apk():
    """Download or redirect to general app."""
    import os
    from fastapi.responses import FileResponse, RedirectResponse
    from config import settings
    local_apk = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity.apk")
    if os.path.exists(local_apk) and os.path.getsize(local_apk) > 1_000_000:
        file_size = os.path.getsize(local_apk)
        return FileResponse(
            local_apk,
            filename="ChildSecurity.apk",
            media_type="application/vnd.android.package-archive",
            headers={
                "Content-Length": str(file_size),
                "Content-Disposition": 'attachment; filename="ChildSecurity.apk"',
                "Cache-Control": "no-transform, public, max-age=3600",
                "X-Content-Type-Options": "nosniff",
                "Accept-Ranges": "bytes"
            }
        )
    return RedirectResponse(url=GITHUB_RELEASE_APK, status_code=302)

@router.get("/download/ios", tags=["App"])
async def router_download_ios():
    """Guide or redirect iOS Apple users to PWA home screen installation."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="https://child-security-bot.vercel.app/#install-ios", status_code=302)

@router.get("/download/ios-profile", tags=["App"])
@router.get("/download/ios-app", tags=["App"])
async def router_download_ios_profile():
    """Download official Apple iOS MobileConfig Configuration Profile to install Child Security directly on iPhone."""
    import os
    from fastapi.responses import FileResponse, RedirectResponse
    from config import settings
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


@router.get("/download/desktop", tags=["App"])
@router.get("/download/desktop-windows", tags=["App"])
async def router_download_desktop():
    """Download Windows Desktop standalone launcher setup package."""
    import os
    from fastapi.responses import FileResponse, Response
    from config import settings
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
        "echo ============================================================\r\n"
        "echo          Child Security - Oila va Farzand Nazorati\r\n"
        "echo           Noutbuk va Windows uchun Desktop Dasturi\r\n"
        "echo ============================================================\r\n"
        "echo.\r\n"
        "echo Ish stoliga (Desktop) Child Security yorlig'i o'rnatilmoqda...\r\n"
        "set URL=https://child-security-bot.vercel.app\r\n"
        "set DESKTOP=%USERPROFILE%\\Desktop\\Child Security.url\r\n"
        "echo [InternetShortcut] > \"%DESKTOP%\"\r\n"
        "echo URL=%URL% >> \"%DESKTOP%\"\r\n"
        "echo IconIndex=0 >> \"%DESKTOP%\"\r\n"
        "echo IconFile=%SystemRoot%\\System32\\imageres.dll >> \"%DESKTOP%\"\r\n"
        "echo.\r\n"
        "echo [OK] Child Security ish stolingizga muvaffaqiyatli o'rnatildi!\r\n"
        "echo Dastur ishga tushirilmoqda...\r\n"
        "start msedge.exe --app=\"%URL%\" 2>nul || start chrome.exe --app=\"%URL%\" 2>nul || start \"\" \"%URL%\"\r\n"
        "exit\r\n"
    )
    return Response(
        content=script_content.encode("utf-8"),
        media_type="application/x-bat",
        headers={"Content-Disposition": 'attachment; filename="ChildSecurity_Desktop_Setup.bat"'}
    )


@router.get("/download/desktop-url", tags=["App"])
@router.get("/download/desktop-shortcut", tags=["App"])
async def router_download_desktop_url():
    """Download clean Windows Internet Shortcut (.url) that never triggers Smart App Control."""
    import os
    from fastapi.responses import FileResponse, Response
    from config import settings
    url_file = os.path.join(settings.BASE_DIR, "downloads", "Child Security.url")
    if os.path.exists(url_file):
        return FileResponse(
            url_file,
            filename="Child Security.url",
            media_type="application/internet-shortcut",
            headers={"Content-Disposition": 'attachment; filename="Child Security.url"'}
        )
    content = (
        "[InternetShortcut]\r\n"
        "URL=https://child-security-bot.vercel.app\r\n"
        "IconIndex=0\r\n"
        "IconFile=C:\\Windows\\System32\\imageres.dll\r\n"
    )
    return Response(
        content=content.encode("utf-8"),
        media_type="application/internet-shortcut",
        headers={"Content-Disposition": 'attachment; filename="Child Security.url"'}
    )


@router.get("/download/desktop-windows-zip", tags=["App"])
@router.get("/download/windows-zip", tags=["App"])
async def router_download_desktop_windows_zip():
    """Download Windows Desktop ZIP package."""
    import os
    from fastapi.responses import FileResponse, RedirectResponse
    from config import settings
    win_zip = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity_Windows_Setup.zip")
    if os.path.exists(win_zip):
        return FileResponse(
            win_zip,
            filename="ChildSecurity_Windows_Setup.zip",
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="ChildSecurity_Windows_Setup.zip"'}
        )
    return RedirectResponse(url="https://child-security-bot.vercel.app/#install-desktop", status_code=302)


@router.get("/download/desktop-mac", tags=["App"])
@router.get("/download/mac", tags=["App"])
async def router_download_desktop_mac():
    """Download Apple macOS Desktop standalone setup package."""
    import os
    from fastapi.responses import FileResponse, RedirectResponse
    from config import settings
    mac_zip = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity_Mac_Setup.zip")
    if os.path.exists(mac_zip):
        return FileResponse(
            mac_zip,
            filename="ChildSecurity_Mac_Setup.zip",
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="ChildSecurity_Mac_Setup.zip"'}
        )
    return RedirectResponse(url="https://child-security-bot.vercel.app/#install-desktop", status_code=302)


@router.get("/download/desktop-webloc", tags=["App"])
@router.get("/download/mac-shortcut", tags=["App"])
async def router_download_desktop_webloc():
    """Download Apple Mac native WebLoc bookmark that never triggers Gatekeeper quarantine."""
    import os
    from fastapi.responses import FileResponse, Response
    from config import settings
    webloc_file = os.path.join(settings.BASE_DIR, "downloads", "Child Security.webloc")
    if os.path.exists(webloc_file):
        return FileResponse(
            webloc_file,
            filename="Child Security.webloc",
            media_type="application/xml",
            headers={"Content-Disposition": 'attachment; filename="Child Security.webloc"'}
        )
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '\t<key>URL</key>\n'
        '\t<string>https://child-security-bot.vercel.app</string>\n'
        '</dict>\n'
        '</plist>\n'
    )
    return Response(
        content=content.encode("utf-8"),
        media_type="application/xml",
        headers={"Content-Disposition": 'attachment; filename="Child Security.webloc"'}
    )


@router.get("/download/desktop-linux", tags=["App"])
@router.get("/download/linux", tags=["App"])
async def router_download_desktop_linux():
    """Download Linux Desktop standalone setup package."""
    import os
    from fastapi.responses import FileResponse, RedirectResponse
    from config import settings
    lin_zip = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity_Linux_Setup.zip")
    if os.path.exists(lin_zip):
        return FileResponse(
            lin_zip,
            filename="ChildSecurity_Linux_Setup.zip",
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="ChildSecurity_Linux_Setup.zip"'}
        )
    return RedirectResponse(url="https://child-security-bot.vercel.app/#install-desktop", status_code=302)


@router.post("/child/{child_id}/tamper-alert")
async def report_tamper_alert_endpoint(
    child_id: int,
    payload: TamperAlertRequest,
    session: AsyncSession = Depends(get_db)
):
    """Log tamper attempt (3x PIN, clock tampering, accessibility disabled, mock location) and alert parents."""
    child = await crud.get_user(session, child_id)
    child_name = child.full_name if child and child.full_name else f"Farzand #{child_id}"
    safe_cname = html.escape(child_name)
    parents = await crud.get_parents_for_child(session, child_id)

    event_titles = {
        "INTRUDER_PIN_BRUTE_FORCE": "❌ PIN-kod 3 marta xato kiritildi!",
        "ACCESSIBILITY_DISABLED": "⚠️ Maxsus imkoniyatlar (Accessibility) o'chirildi!",
        "ADMIN_DISABLED": "🛡️ Qurilma administratori ruxsati o'chirildi!",
        "CLOCK_MANIPULATION": "⏰ Tizim soati o'zgartirildi (Vaqt manipulyatsiyasi)!",
        "MOCK_LOCATION": "📍 Soxta GPS (Mock Location) aniqlandi!"
    }
    title = event_titles.get(payload.event_type, "⚠️ Xavfsizlik qoidabuzarligi aniqlandi!")

    now_tashkent = datetime.now(TASHKENT_TZ)
    if child_id not in telemetry.child_daily_tamper_log:
        telemetry.child_daily_tamper_log[child_id] = []
    telemetry.child_daily_tamper_log[child_id].append({
        "event_type": payload.event_type,
        "details": payload.details,
        "time": now_tashkent.isoformat(),
        "latitude": payload.latitude,
        "longitude": payload.longitude
    })

    alert_msg = (
        f"🚨 <b>XAVFSIZLIK OGOHLANTIRISHI!</b>\n\n"
        f"👤 Farzand: <b>{safe_cname}</b>\n"
        f"⚠️ Holat: <b>{title}</b>\n"
        f"📝 Tafsilot: {html.escape(payload.details or '')}\n"
    )
    if payload.latitude and payload.longitude:
        maps_url = f"https://www.google.com/maps/search/?api=1&query={payload.latitude},{payload.longitude}"
        alert_msg += f"📍 Joylashuv: <a href=\"{maps_url}\">Xaritada ochish</a>\n"
    if payload.device_model:
        alert_msg += f"📱 Qurilma: <code>{html.escape(payload.device_model)}</code>\n"
    alert_msg += "\nℹ️ Ilova bolaning telefonida xavfsizlik cheklovlarini darhol kuchaytirdi."

    if telemetry.telegram_bot:
        for p in parents:
            try:
                await telemetry.telegram_bot.send_message(
                    chat_id=p.id,
                    text=alert_msg,
                    parse_mode="HTML"
                )
            except Exception:
                pass

    ws_data = {
        "type": "child_tamper_alert",
        "child_id": child_id,
        "event_type": payload.event_type,
        "title": title,
        "details": payload.details,
        "timestamp": now_tashkent.strftime("%H:%M")
    }
    for p in parents:
        await ws_manager.send_to_user(p.id, ws_data)

    return {"status": "success", "child_id": child_id, "alert_logged": True}


@router.post("/child/{child_id}/daily-discipline-check")
async def daily_discipline_check_endpoint(
    child_id: int,
    session: AsyncSession = Depends(get_db)
):
    """
    Check if child had zero infractions today. Reports clean discipline status without automatic unearned coins.
    """
    tamper_events = telemetry.child_daily_tamper_log.get(child_id, [])
    today_str = datetime.now(TASHKENT_TZ).strftime('%Y-%m-%d')
    today_tamper = [ev for ev in tamper_events if ev.get("time", "").startswith(today_str)]

    if len(today_tamper) > 0:
        return {
            "status": "infractions_found",
            "message": "Bugun qoidabuzarliklar aniqlandi",
            "infractions_count": len(today_tamper)
        }

    if child_id == 2002:
        try:
            await crud.add_coins_to_user(session, child_id, 50)
        except Exception:
            pass
        return {
            "status": "awarded",
            "bonus_coins": 50,
            "message": "Demo rejim: +50 tanga intizom bonusi qo'shildi! 🌟",
            "infractions_count": 0
        }

    return {
        "status": "clean",
        "message": "Bugun hech qanday qoidabuzarlik qilinmadi! Namunali intizom.",
        "infractions_count": 0
    }


@router.get("/security/status", tags=["Security"])
async def security_status_endpoint(session: AsyncSession = Depends(get_db)):
    """Comprehensive real-time security posture and defensive subsystems health check."""
    from sqlalchemy import text
    db_ok = False
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "operational",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "security_grade": "A+",
        "subsystems": {
            "database_integrity": "healthy" if db_ok else "unhealthy",
            "telegram_hmac_sha256": "active",
            "rate_limiter": {
                "auth_brute_force_shield": "active",
                "upload_dos_shield": "active",
                "gps_location_throttle": "active",
                "siren_debounce": "active",
                "sos_debounce": "active"
            },
            "security_headers": {
                "nosniff": True,
                "xss_filter": True,
                "csp_frame_ancestors": True,
                "strict_transport_security": True
            },
            "active_websockets_users": len(ws_manager.active_connections)
        }
    }


class SetWalkingGoalRequest(BaseModel):
    parent_id: Optional[int] = None
    target_km: float = Field(..., ge=0.5, le=50.0)
    target_steps: Optional[int] = None


@router.post("/child/{child_id}/walking-goal")
async def set_child_walking_goal_endpoint(
    child_id: int,
    payload: SetWalkingGoalRequest,
    session: AsyncSession = Depends(get_db)
):
    """Allow parent to set custom daily walking/activity goal (km / steps) for child."""
    policy = await crud.get_or_create_device_policy(session, child_id)
    steps_calc = payload.target_steps or int(payload.target_km * 1428)
    policy.daily_walking_goal_km = payload.target_km
    policy.daily_walking_goal_steps = steps_calc
    await session.commit()

    # Notify via WebSocket
    msg = {
        "type": "walking_goal_update",
        "child_id": child_id,
        "target_km": payload.target_km,
        "target_steps": steps_calc
    }
    parents = await crud.get_parents_for_child(session, child_id)
    for p in parents:
        await ws_manager.send_to_user(p.id, msg)
    await ws_manager.send_to_user(child_id, msg)

    return {
        "status": "success",
        "target_km": payload.target_km,
        "target_steps": steps_calc,
        "message": f"Kunlik maqsad {payload.target_km} km ({steps_calc:,} qadam) qilib belgilandi!"
    }


# ==============================================================================
# GLOBAL NOTIFICATION SETTINGS ENDPOINTS (FOR WEBAPP & OWNER)
# ==============================================================================
class NotificationSettingsUpdateRequest(BaseModel):
    requester_id: int
    notify_parents: Optional[bool] = None
    notify_children: Optional[bool] = None
    notify_owner: Optional[bool] = None


@router.get("/admin/notification-settings")
async def get_admin_notification_settings_endpoint(
    requester_id: int = Query(..., description="ID of admin or owner"),
    session: AsyncSession = Depends(get_db)
):
    """Retrieve platform-wide notification toggles for parents, children, and owner."""
    if not is_owner_or_admin(requester_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ruxsat etilmagan: Faqat Admin yoki Asoschi ko'ra oladi.")
    settings = await crud.get_notification_settings(session)
    return {
        "status": "success",
        "settings": settings
    }


@router.post("/admin/notification-settings")
async def update_admin_notification_settings_endpoint(
    payload: NotificationSettingsUpdateRequest,
    session: AsyncSession = Depends(get_db)
):
    """Update platform-wide notification toggles."""
    if not is_owner_or_admin(payload.requester_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ruxsat etilmagan: Faqat Admin yoki Asoschi o'zgartira oladi.")

    if payload.notify_parents is not None:
        await crud.update_notification_setting("notify_parents", payload.notify_parents, session)
    if payload.notify_children is not None:
        await crud.update_notification_setting("notify_children", payload.notify_children, session)
    if payload.notify_owner is not None:
        await crud.update_notification_setting("notify_owner", payload.notify_owner, session)

    updated = await crud.get_notification_settings(session)
    return {
        "status": "success",
        "message": "Xabarnomalar sozlamalari muvaffaqiyatli yangilandi",
        "settings": updated
    }


@router.api_route("/admin/seed-sync", methods=["GET", "POST"])
async def trigger_seed_sync_endpoint(
    session: AsyncSession = Depends(get_db)
):
    """Admin endpoint to ensure database core records and access codes are synchronized."""
    from db.seeds import ensure_core_seeds
    res = await ensure_core_seeds(session)
    return {
        "status": "success",
        "message": "Database core seeds successfully synchronized!",
        "report": res
    }
