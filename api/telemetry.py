import logging
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


def set_bot_instance(bot: Bot):
    global telegram_bot
    telegram_bot = bot


class TelemetryPayload(BaseModel):
    child_id: int = Field(..., description="Telegram ID of the child")
    lat: float = Field(..., description="Latitude coordinate", ge=-90.0, le=90.0)
    lon: float = Field(..., description="Longitude coordinate", ge=-180.0, le=180.0)
    battery_level: Optional[int] = Field(None, description="Battery percentage (0-100)", ge=0, le=100)
    is_charging: bool = Field(False, description="Whether phone is plugged into charger")
    sound_mode: str = Field("normal", description="Phone sound mode: normal, vibrate, silent")
    accuracy: Optional[float] = Field(None, description="GPS horizontal accuracy in meters")
    timestamp: Optional[str] = Field(None, description="ISO timestamp or client timestamp")


@router.post("/telemetry", status_code=status.HTTP_200_OK)
async def receive_telemetry(payload: TelemetryPayload, session: AsyncSession = Depends(get_db)):
    """
    Receive background telemetry pings from child's device:
    GPS coordinates, battery level, charging status, sound profile.
    Automatically verifies battery threshold and geofence boundary events.
    """
    child = await crud.get_user(session, payload.child_id)
    if not child:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Child with ID {payload.child_id} not registered in system"
        )

    # 1. Log Location
    loc_entry = await crud.log_location(
        session=session,
        child_id=payload.child_id,
        latitude=payload.lat,
        longitude=payload.lon,
        accuracy=payload.accuracy,
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
            child_name = child.full_name or f"Farzand ID:{child.id}"
            alert_msg = (
                f"🪫 **OGOHLANTIRISH: QUVVAT KAM QOLDI!**\n\n"
                f"👤 Farzandingiz: **{child_name}**\n"
                f"🔋 Batareya darajasi: **{payload.battery_level}%**\n"
                f"⚡ Holat: Quvvatlanmayapti\n\n"
                f"Iltimos, farzandingiz bilan bog'lanib, telefonini quvvatga qo'yishini eslating."
            )
            for p in parents:
                try:
                    await telegram_bot.send_message(
                        chat_id=p.id,
                        text=alert_msg,
                        parse_mode="Markdown"
                    )
                    battery_alert_triggered = True
                except Exception as e:
                    logger.error(f"Failed to dispatch battery alert to parent {p.id}: {e}")

    # 3. Geofence Boundary Check (Entry / Exit transitions)
    geofence_events = await evaluate_geofences_and_notify(
        bot=telegram_bot,
        session=session,
        child=child,
        lat=payload.lat,
        lon=payload.lon
    )

    # 4. If child is in active SOS state, alert parents with latest pin
    if child.is_sos_active and telegram_bot:
        parents = await crud.get_parents_for_child(session, payload.child_id)
        for p in parents:
            try:
                await telegram_bot.send_location(
                    chat_id=p.id,
                    latitude=payload.lat,
                    longitude=payload.lon
                )
            except Exception as e:
                logger.error(f"Failed to push SOS update to parent {p.id}: {e}")

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
                "speed": None,
                "battery_level": payload.battery_level,
                "is_charging": payload.is_charging,
                "sound_mode": payload.sound_mode,
                "recorded_at": loc_entry.recorded_at.isoformat() if loc_entry and loc_entry.recorded_at else None
            }
            for p in parents:
                await ws_manager.send_to_user(p.id, ws_telemetry)
    except Exception as ws_err:
        logger.error(f"Failed to broadcast telemetry via WebSocket: {ws_err}")

    return {
        "status": "success",
        "message": "Telemetry processed successfully",
        "data": {
            "log_id": loc_entry.id,
            "child_id": payload.child_id,
            "battery_alert_triggered": battery_alert_triggered,
            "geofence_events": geofence_events,
            "is_sos_active": child.is_sos_active
        }
    }
