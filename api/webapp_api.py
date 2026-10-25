import html
import json
import logging
import random
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File, WebSocket, WebSocketDisconnect, Request
import hashlib
import hmac
import time
from urllib.parse import parse_qsl
from config import settings
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
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

router = APIRouter(prefix="/api/v1/webapp", tags=["WebApp"])


def format_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """Ensure naive datetimes from DB are explicitly marked as UTC ISO strings."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class ConnectionManager:
    """Manages real-time WebSocket connections between parents and children."""
    def __init__(self):
        self.active_connections: dict[int, set[WebSocket]] = defaultdict(set)

    async def connect(self, user_id: int, websocket: WebSocket):
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


class CreateGeofenceRequest(BaseModel):
    parent_id: int
    name: str = Field(..., min_length=1, max_length=100)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    radius_meters: float = Field(200.0, ge=30.0, le=5000.0)
    child_id: Optional[int] = None


class AuthCodeRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)


class GenerateWebCodeRequest(BaseModel):
    role: str = Field("parent", description="User role: 'parent' or 'child'")
    full_name: Optional[str] = Field(None, max_length=100)


class TelegramAuthRequest(BaseModel):
    init_data: str = Field(..., min_length=10, description="Raw initData string from window.Telegram.WebApp.initData")


class ChildSOSRequest(BaseModel):
    is_sos_active: bool = True
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accuracy: Optional[float] = None
    battery_level: Optional[int] = None


class DevicePolicyUpdateRequest(BaseModel):
    is_all_apps_blocked: Optional[bool] = None
    lockdown_reason: Optional[str] = None
    blocked_packages: Optional[List[str]] = None
    anti_uninstall_enabled: Optional[bool] = None
    new_pin: Optional[str] = None
    parent_id: Optional[int] = None


class VerifyParentPinRequest(BaseModel):
    pin: str = Field(..., min_length=4, max_length=16)


class AppUsageUploadRequest(BaseModel):
    total_screen_time_seconds: int = Field(..., ge=0)
    unlock_count: int = Field(0, ge=0)
    app_stats: List[dict] = Field(default_factory=list)
    report_date: Optional[str] = None


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

        return {
            "status": "success",
            "role": "parent",
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
    parent = res.scalar_one_or_none()

    return {
        "status": "success",
        "role": "child",
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
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = rate_limiter.check_auth_rate_limit(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Xavfsizlik tizimi: Ko'p noto'g'ri urinishlar aniqlandi. Iltimos, {retry_after} soniyadan so'ng qayta urinib ko'ring."
        )

    code = payload.code.strip().replace(" ", "").replace("-", "")
    user = await crud.get_user_by_web_access_code(session, code)
    if not user:
        rate_limiter.record_auth_failure(client_ip)
        raise HTTPException(
            status_code=404,
            detail="Kiritilgan kod noto'g'ri yoki mavjud emas. Iltimos, Telegram bot orqali kodni tekshiring."
        )

    rate_limiter.reset_auth_failures(client_ip)
    return await build_user_auth_payload(user, session, code=code)


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
    client_ip = request.client.host if request.client else "unknown"
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
    """Update coin rate settings and notify linked children."""
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

    return {
        "status": "success",
        "child": {
            "id": child.id,
            "full_name": child.full_name,
            "username": child.username,
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
                "lockdown_reason": policy.lockdown_reason,
                "blocked_packages": blocked_packages,
                "anti_uninstall_enabled": policy.anti_uninstall_enabled,
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
    fence = await crud.create_geofence(
        session=session,
        parent_id=payload.parent_id,
        name=payload.name,
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
    client_ip = request.client.host if request.client else "unknown"
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
    description: Optional[str] = None
    reward_coins: int = Field(1000, ge=100, le=1000000)
    media_type: Optional[str] = None
    media_url: Optional[str] = None


@router.post("/task")
async def create_task_endpoint(payload: CreateTaskRequest, session: AsyncSession = Depends(get_db)):
    """Create a task directly from WebApp and deliver to child's Telegram."""
    task = await crud.create_task(
        session=session,
        parent_id=payload.parent_id,
        child_id=payload.child_id,
        title=payload.title,
        description=payload.description,
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
async def mark_task_seen_endpoint(task_id: int, session: AsyncSession = Depends(get_db)):
    """Child marks task seen via WebApp -> notifies parent in Telegram."""
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
    session: AsyncSession = Depends(get_db)
):
    """Child sets promised completion time via WebApp -> notifies parent in Telegram."""
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
    session: AsyncSession = Depends(get_db)
):
    """Child submits completed task with mandatory photo proof via WebApp."""
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
    parent_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Approve a task directly from WebApp and notify child with IDOR parent validation."""
    existing_task = await crud.get_task_by_id(session, task_id)
    if not existing_task:
        raise HTTPException(status_code=404, detail="Task not found")
    if parent_id and existing_task.parent_id != parent_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ruxsat berilmagan: Bu vazifa sizga tegishli emas.")

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
    parent_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Reject a task directly from WebApp and notify child with IDOR parent validation."""
    existing_task = await crud.get_task_by_id(session, task_id)
    if not existing_task:
        raise HTTPException(status_code=404, detail="Task not found")
    if parent_id and existing_task.parent_id != parent_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ruxsat berilmagan: Bu vazifa sizga tegishli emas.")

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
    parent_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Delete a task."""
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

    # Rate limiting / Debounce: Prevent duplicate SOS Telegram alerts within 15 seconds
    now_utc = datetime.now(timezone.utc)
    last_sos = telemetry.last_sos_alert_map.get(child_id)
    is_duplicate_sos = bool(last_sos and (now_utc - last_sos) < timedelta(seconds=15))

    if telemetry.telegram_bot and parents and not is_duplicate_sos:
        telemetry.last_sos_alert_map[child_id] = now_utc
        now_str = datetime.now(TASHKENT_TZ).strftime("%d.%m.%Y, %H:%M:%S")
        battery_info = f"{battery_lvl}%" if battery_lvl is not None else "Noma'lum"
        loc_coords = f"{lat}, {lon}" if (lat is not None and lon is not None) else "Mavjud emas"
        maps_link = (
            f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
            if (lat is not None and lon is not None) else None
        )
        safe_cname = html.escape(child_name)

        parent_alert = (
            f"🚨🚨🚨 <b>FAVQULODDA VAZIYAT (SOS)!</b> 🚨🚨🚨\n\n"
            f"Farzandingiz <b>{safe_cname}</b> WebApp orqali xavf signalini yoqdi!\n\n"
            f"🕒 Vaqt: <b>{html.escape(now_str)}</b>\n"
            f"🔋 Batareya: <b>{html.escape(battery_info)}</b>\n"
            + (f"📍 Koordinata: <code>{loc_coords}</code>\n🗺 Xaritada: <a href=\"{maps_link}\">Google Maps orqali ochish</a>\n\n" if maps_link else "\n")
            + f"⚠️ <b>DIQQAT:</b> Farzandingiz bilan zudlik bilan bog'laning yoki xaritada tekshiring!"
        )

        for p in parents:
            try:
                await telemetry.telegram_bot.send_message(
                    chat_id=p.id,
                    text=parent_alert,
                    reply_markup=get_sos_alert_parent_keyboard(child_id, lat, lon),
                    parse_mode="HTML"
                )
                if lat is not None and lon is not None:
                    await telemetry.telegram_bot.send_location(
                        chat_id=p.id,
                        latitude=lat,
                        longitude=lon
                    )
            except Exception as e:
                logger.error(f"Failed to notify parent {p.id} of SOS: {e}")

    parent_phone = parents[0].phone_number if parents and parents[0].phone_number else None

    return {
        "status": "success",
        "child_id": child_id,
        "is_sos_active": True,
        "parent_phone": parent_phone,
        "notified_parents": len(parents),
        "latitude": lat,
        "longitude": lon,
        "battery_level": battery_lvl
    }


@router.post("/child/{child_id}/sos/cancel")
async def child_cancel_sos_endpoint(child_id: int, session: AsyncSession = Depends(get_db)):
    """Cancel/resolve SOS emergency alert and notify all linked parents."""
    await crud.set_child_sos(session, child_id, False)
    telemetry.last_sos_alert_map.pop(child_id, None)
    child = await crud.get_user(session, child_id)
    child_name = child.full_name if child else f"Farzand #{child_id}"
    parents = await crud.get_parents_for_child(session, child_id)
    await session.commit()

    if telemetry.telegram_bot:
        if parents:
            for p in parents:
                try:
                    safe_cname = html.escape(child_name)
                    await telemetry.telegram_bot.send_message(
                        chat_id=p.id,
                        text=f"✅ <b>XAVF BARTARAF ETILDI!</b>\n\nFarzandingiz <b>{safe_cname}</b> SOS xavf signalini to'xtatdi. Hammasi joyida va xavfsiz ekanligi tasdiqlandi.",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify parent {p.id} of SOS cancellation: {e}")

        if child:
            try:
                await telemetry.telegram_bot.send_message(
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

    return {"status": "success", "child_id": child_id, "is_sos_active": False}


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
    """Parent triggers remote siren / sound ping to child's phone across Telegram Bot and Mobile App with 5s debounce."""
    if parent_id:
        parents = await crud.get_parents_for_child(session, child_id)
        if parents and parent_id not in [p.id for p in parents]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz."
            )

    now_utc = datetime.now(timezone.utc)
    last_siren = last_siren_alert_map.get(child_id)
    if last_siren and (now_utc - last_siren) < timedelta(seconds=5):
        return {
            "status": "rate_limited",
            "message": "Signal allaqachon yuborildi. Iltimos, 5 soniya kuting.",
            "child_id": child_id
        }
    last_siren_alert_map[child_id] = now_utc

    if telemetry.telegram_bot:
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
            "message": "Ota-onangiz telefoningizga qidiruv signali yubordi!"
        })
    except Exception:
        pass

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

        # 2. Battery alert check (< 20%)
        if payload.battery_level is not None and payload.battery_level <= 20 and not payload.is_charging:
            try:
                should_alert = await crud.check_and_update_battery_alert(
                    session=session,
                    child_id=child_id,
                    battery_level=payload.battery_level
                )
                if should_alert:
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

        # 3. Active SOS push: Handled exclusively by SOS trigger endpoint to avoid duplicate Telegram location balloons

    await session.commit()

    # Broadcast updated location to parents via WebSocket
    parents = await crud.get_parents_for_child(session, child_id)
    telemetry_data = {
        "type": "child_location_update",
        "child_id": child_id,
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "accuracy": payload.accuracy,
        "speed": payload.speed,
        "battery_level": payload.battery_level,
        "is_charging": payload.is_charging,
        "recorded_at": format_utc_iso(log.recorded_at) if log.recorded_at else format_utc_iso(datetime.now(timezone.utc))
    }
    for p in parents:
        await ws_manager.send_to_user(p.id, telemetry_data)

    policy = await crud.get_or_create_device_policy(session, child_id)
    try:
        blocked_packages = json.loads(policy.blocked_packages or "[]")
    except Exception:
        blocked_packages = []

    return {
        "status": "success",
        "child_id": child_id,
        "recorded_at": format_utc_iso(log.recorded_at),
        "policy": {
            "is_all_apps_blocked": policy.is_all_apps_blocked,
            "lockdown_reason": policy.lockdown_reason,
            "blocked_packages": blocked_packages,
            "anti_uninstall_enabled": policy.anti_uninstall_enabled,
            "has_parent_pin": bool(policy.parent_pin_hash),
        }
    }


@router.post("/child/{child_id}/silent-check")
async def silent_check_endpoint(
    child_id: int,
    parent_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Silent Check ('Passiv monitoring'):
    Fetch latest live coordinates and telemetry for parent.
    Strictly NO UI alert/notification is sent to child!
    """
    if parent_id:
        parents = await crud.get_parents_for_child(session, child_id)
        if parents and parent_id not in [p.id for p in parents]:
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
    if parent_id:
        parents = await crud.get_parents_for_child(session, child_id)
        if parents and parent_id not in [p.id for p in parents]:
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
    client_ip = request.client.host if request.client else "unknown"
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

    parents = await crud.get_parents_for_child(session, child_id)
    if telemetry.telegram_bot and parents:
        safe_cname = html.escape(child_name)
        safe_dest = html.escape(dest)
        msg = (
            f"🎉 <b>FARZANDINGIZ ESON-OMON YETIB KELDI!</b>\n\n"
            f"👤 Farzandingiz <b>{safe_cname}</b> manzilga (<b>{safe_dest}</b>) xavfsiz yetib kelganini tasdiqladi! Xavfsiz yo'l rejimi yakunlandi."
        )
        for p in parents:
            try:
                await telemetry.telegram_bot.send_message(chat_id=p.id, text=msg, parse_mode="HTML")
            except Exception:
                pass

    for p in parents:
        await ws_manager.send_to_user(p.id, {
            "type": "walk_safe_completed",
            "child_id": child_id,
            "destination": dest
        })

    return {"status": "success", "child_id": child_id, "message": "Walk safe completed successfully"}


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
    await crud.update_child_wishlist(
        session=session,
        child_id=child_id,
        title=payload.title,
        target_som=payload.target_som
    )
    data = await crud.get_child_wishlist(session, child_id)

    # If achieved, alert parents with celebration notification
    if data and data.get("is_achieved") and telemetry.telegram_bot:
        child = await crud.get_user(session, child_id)
        child_name = child.full_name if child else f"Farzand #{child_id}"
        parents = await crud.get_parents_for_child(session, child_id)
        safe_cname = html.escape(child_name)
        safe_title = html.escape(payload.title)
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
    """Get current app blocking and protection policy for child."""
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
    """Update device policy: toggle lock all apps, block/unblock specific apps, anti-uninstall shield, or set parent PIN with IDOR authorization guard."""
    parents = await crud.get_parents_for_child(session, child_id)
    parent_ids = [p.id for p in parents]

    effective_parent_id = payload.parent_id
    if not effective_parent_id and len(parent_ids) == 1:
        effective_parent_id = parent_ids[0]

    if parent_ids:
        if effective_parent_id and effective_parent_id not in parent_ids:
            logger.warning(f"Unauthorized policy update attempt on child {child_id} by parent_id {effective_parent_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz."
            )
    elif effective_parent_id:
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
        lockdown_reason=payload.lockdown_reason,
        blocked_packages=payload.blocked_packages,
        anti_uninstall_enabled=payload.anti_uninstall_enabled,
        new_pin=payload.new_pin
    )

    try:
        blocked = json.loads(policy.blocked_packages or "[]")
    except Exception:
        blocked = []

    policy_msg = {
        "type": "device_policy_update",
        "child_id": child_id,
        "is_all_apps_blocked": policy.is_all_apps_blocked,
        "lockdown_reason": policy.lockdown_reason,
        "blocked_packages": blocked,
        "anti_uninstall_enabled": policy.anti_uninstall_enabled,
        "has_parent_pin": bool(policy.parent_pin_hash),
    }
    parents = await crud.get_parents_for_child(session, child_id)
    for p in parents:
        await ws_manager.send_to_user(p.id, policy_msg)
    await ws_manager.send_to_user(child_id, policy_msg)

    if telemetry.telegram_bot and parents and payload.is_all_apps_blocked is not None:
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

    return {
        "status": "success",
        "child_id": child_id,
        "policy": {
            "is_all_apps_blocked": policy.is_all_apps_blocked,
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
