import html
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot

from db.database import get_db
import db.crud as crud
from bot.geofence import evaluate_geofences_and_notify

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Telemetry"])

# Global bot instance holder that can be set from main.py
telegram_bot: Optional[Bot] = None

# Speed alert rate-limiting map: child_id -> datetime of last alert
last_speed_alert_map: dict[int, datetime] = {}

# SOS alert rate-limiting map: child_id -> datetime of last SOS alert (prevents duplicate triggers)
last_sos_alert_map: dict[int, datetime] = {}

# Location forward rate-limiting map: child_id -> datetime of last location forwarded to parent
last_loc_forward_map: dict[int, datetime] = {}

# Telemetry ping throttle map: child_id -> timestamp (prevents GPS flooding)
last_telemetry_ping_map: dict[int, float] = {}

# Tamper incidents log: child_id -> list of tamper dicts
child_daily_tamper_log: dict[int, list[dict]] = {}

# Track discipline bonus awarded date: (child_id, date_str) -> bool
discipline_bonus_awarded: set[str] = set()

# Speed change tracker for crash / sudden deceleration: child_id -> (speed, timestamp)
last_known_speed_tracker: dict[int, tuple[float, float]] = {}
last_impact_alert_map: dict[int, float] = {}


def set_bot_instance(bot: Bot):
    global telegram_bot
    telegram_bot = bot


class TelemetryPayload(BaseModel):
    child_id: int = Field(..., description="Telegram ID of the child")
    lat: float = Field(..., description="Latitude coordinate", ge=-90.0, le=90.0)
    lon: float = Field(..., description="Longitude coordinate", ge=-180.0, le=180.0)
    speed: Optional[float] = Field(None, description="Current movement speed in km/h", ge=0.0, le=500.0)
    battery_level: Optional[int] = Field(None, description="Battery percentage (0-100)", ge=0, le=100)
    is_charging: bool = Field(False, description="Whether phone is plugged into charger")
    sound_mode: str = Field("normal", description="Phone sound mode: normal, vibrate, silent")
    accuracy: Optional[float] = Field(None, description="GPS horizontal accuracy in meters", ge=0.0, le=50000.0)
    timestamp: Optional[str] = Field(None, description="ISO timestamp or client timestamp")


@router.post("/telemetry", status_code=status.HTTP_200_OK)
async def receive_telemetry(payload: TelemetryPayload, session: AsyncSession = Depends(get_db)):
    """
    Receive background telemetry pings from child's device:
    GPS coordinates, speed, battery level, charging status, sound profile.
    Automatically verifies battery threshold, speed limits, and geofence boundary events.
    """
    # 0. Anti-Flood & GPS Telemetry Rate Limiting (Throttle < 1.0s)
    import time
    now_ts = time.time()
    last_ping = last_telemetry_ping_map.get(payload.child_id, 0.0)
    if (now_ts - last_ping) < 1.0:
        return {"status": "throttled", "message": "Pings too frequent, min interval is 1s."}
    last_telemetry_ping_map[payload.child_id] = now_ts

    child = await crud.get_user(session, payload.child_id)
    if not child:
        child, _ = await crud.get_or_create_user(session, payload.child_id, role="child")

    # 1. Log Location
    loc_entry = await crud.log_location(
        session=session,
        child_id=payload.child_id,
        latitude=payload.lat,
        longitude=payload.lon,
        accuracy=payload.accuracy,
        speed=payload.speed,
        battery_level=payload.battery_level,
        is_charging=payload.is_charging,
        sound_mode=payload.sound_mode,
        source="api_telemetry"
    )

    # 2. Battery Alert Check (< 20%)
    battery_alert_triggered = False
    if payload.battery_level is not None and payload.battery_level <= 20 and not payload.is_charging:
        should_alert = await crud.check_and_update_battery_alert(
            session=session,
            child_id=payload.child_id,
            battery_level=payload.battery_level
        )
        if should_alert and telegram_bot:
            parents = await crud.get_parents_for_child(session, payload.child_id)
            child_name = html.escape(child.full_name or f"Farzand ID:{child.id}")
            alert_msg = (
                f"🪫 <b>OGOHLANTIRISH: QUVVAT KAM QOLDI!</b>\n\n"
                f"👤 Farzandingiz: <b>{child_name}</b>\n"
                f"🔋 Batareya darajasi: <b>{payload.battery_level}%</b>\n"
                f"⚡ Holat: Quvvatlanmayapti\n\n"
                f"Iltimos, farzandingiz bilan bog'lanib, telefonini quvvatga qo'yishini eslating."
            )
            for p in parents:
                try:
                    await telegram_bot.send_message(
                        chat_id=p.id,
                        text=alert_msg,
                        parse_mode="HTML"
                    )
                    battery_alert_triggered = True
                except Exception as e:
                    logger.error(f"Failed to dispatch battery alert to parent {p.id}: {e}")

    # 2.5 Speed Alert Check (Transport movement >= 30 km/h)
    speed_alert_triggered = False
    if payload.speed is not None and payload.speed >= 30.0 and telegram_bot:
        now = datetime.now(timezone.utc)
        last_alert_time = last_speed_alert_map.get(payload.child_id)
        if not last_alert_time or (now - last_alert_time) > timedelta(minutes=15):
            parents = await crud.get_parents_for_child(session, payload.child_id)
            child_name = html.escape(child.full_name or f"Farzand ID:{child.id}")
            maps_url = f"https://www.google.com/maps/search/?api=1&query={payload.lat},{payload.lon}"
            speed_msg = (
                f"🚗 <b>TEZLIK OGOHLANTIRISHI!</b>\n\n"
                f"👤 Farzandingiz: <b>{child_name}</b>\n"
                f"🚀 Harakat tezligi: <b>{payload.speed:.0f} km/soat</b>\n"
                f"📍 Holat: <b>Avtomobil yoki jamoat transportida harakatlanmoqda</b>\n\n"
                f"🗺 <a href=\"{maps_url}\">Xaritada ko'rish (Google Maps)</a>"
            )
            for p in parents:
                try:
                    await telegram_bot.send_message(
                        chat_id=p.id,
                        text=speed_msg,
                        parse_mode="HTML"
                    )
                    speed_alert_triggered = True
                except Exception as e:
                    logger.error(f"Failed to dispatch speed alert to parent {p.id}: {e}")
            last_speed_alert_map[payload.child_id] = now

    # 2.6 Sudden Impact / Emergency Stop Alert (>40 km/h dropped to < 5 km/h in <= 4s)
    prev_speed_data = last_known_speed_tracker.get(payload.child_id)
    curr_speed = payload.speed or 0.0
    if prev_speed_data and telegram_bot:
        prev_spd, prev_time = prev_speed_data
        dt = now_ts - prev_time
        last_impact_time = last_impact_alert_map.get(payload.child_id, 0.0)
        if prev_spd >= 40.0 and curr_speed < 5.0 and 0.5 <= dt <= 4.5 and (now_ts - last_impact_time) > 300.0:
            parents = await crud.get_parents_for_child(session, payload.child_id)
            child_name = html.escape(child.full_name or f"Farzand ID:{child.id}")
            maps_url = f"https://www.google.com/maps/search/?api=1&query={payload.lat},{payload.lon}"
            impact_msg = (
                f"🚨 <b>OGOHLANTIRISH: KESKIN TO'XTASH ANIQLANDI!</b>\n\n"
                f"👤 Farzandingiz: <b>{child_name}</b>\n"
                f"⚠️ Harakatlanayotgan transport tezligi <b>{prev_spd:.0f} km/soat</b>dan <b>{curr_speed:.0f} km/soat</b>ga keskin tushdi ({dt:.1f} soniya ichida).\n"
                f"📍 Joylashuv: <a href=\"{maps_url}\">Xaritada ko'rish</a>\n\n"
                f"Iltimos, farzandingiz bilan bog'lanib, xavfsizligiga ishonch hosil qiling."
            )
            for p in parents:
                try:
                    await telegram_bot.send_message(chat_id=p.id, text=impact_msg, parse_mode="HTML")
                except Exception as e:
                    logger.debug(f"Impact alert delivery notice: {e}")
            last_impact_alert_map[payload.child_id] = now_ts
    last_known_speed_tracker[payload.child_id] = (curr_speed, now_ts)

    # 3. Geofence Boundary Check (Entry / Exit transitions)
    geofence_events = await evaluate_geofences_and_notify(
        bot=telegram_bot,
        session=session,
        child=child,
        lat=payload.lat,
        lon=payload.lon
    )

    await session.commit()

    # 5. Broadcast live telemetry update to parents viewing WebApp
    try:
        from api.webapp_api import ws_manager
        parents = await crud.get_parents_for_child(session, payload.child_id)
        if parents:
            ws_telemetry = {
                "type": "child_location_update",
                "child_id": payload.child_id,
                "latitude": payload.lat,
                "longitude": payload.lon,
                "accuracy": payload.accuracy,
                "speed": payload.speed,
                "battery_level": payload.battery_level,
                "is_charging": payload.is_charging,
                "sound_mode": payload.sound_mode,
                "recorded_at": loc_entry.recorded_at.isoformat() if loc_entry and loc_entry.recorded_at else None
            }
            for p in parents:
                await ws_manager.send_to_user(p.id, ws_telemetry)
    except Exception as ws_err:
        logger.error(f"Failed to broadcast telemetry via WebSocket: {ws_err}")

    # Fetch active device policy to return to native Android service
    import json
    policy = await crud.get_or_create_device_policy(session, payload.child_id)
    try:
        blocked_packages = json.loads(policy.blocked_packages or "[]")
    except Exception:
        blocked_packages = []

    policy_data = {
        "is_all_apps_blocked": policy.is_all_apps_blocked,
        "is_network_restricted": getattr(policy, "is_network_restricted", False),
        "lockdown_reason": policy.lockdown_reason,
        "blocked_packages": blocked_packages,
        "anti_uninstall_enabled": policy.anti_uninstall_enabled,
        "bedtime_start": getattr(policy, "bedtime_start", None),
        "bedtime_end": getattr(policy, "bedtime_end", None),
        "has_parent_pin": bool(policy.parent_pin_hash),
    }

    return {
        "status": "success",
        "message": "Telemetry processed successfully",
        "policy": policy_data,
        "data": {
            "log_id": loc_entry.id,
            "child_id": payload.child_id,
            "battery_alert_triggered": battery_alert_triggered,
            "geofence_events": geofence_events,
            "is_sos_active": child.is_sos_active,
            "policy": policy_data
        }
    }
