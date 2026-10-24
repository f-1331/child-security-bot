import math
from datetime import datetime
from typing import List, Dict, Any, Optional
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import User
import db.crud as crud


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points
    on the earth (specified in decimal degrees).
    Returns distance in meters. Safe against None and numerical overflow.
    """
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return 999999999.0

    R = 6371000.0  # Earth radius in meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))

    return R * c


async def evaluate_geofences_and_notify(
    bot: Optional[Bot],
    session: AsyncSession,
    child: User,
    lat: float,
    lon: float
) -> List[Dict[str, Any]]:
    """
    Evaluate child's coordinates against all active safe zones (geofences).
    Detects entry and exit transitions and dispatches instant Telegram alerts to parents.
    """
    events: List[Dict[str, Any]] = []
    if lat is None or lon is None:
        return events

    active_fences = await crud.get_active_geofences_for_child(session, child.id)
    if not active_fences:
        return events

    parents = await crud.get_parents_for_child(session, child.id)
    child_name = child.full_name or child.username or f"Farzand ID:{child.id}"
    now_str = datetime.now().strftime("%H:%M")

    for fence in active_fences:
        if fence.latitude is None or fence.longitude is None:
            continue

        dist = haversine_distance(lat, lon, fence.latitude, fence.longitude)
        is_inside_now = dist <= fence.radius_meters

        state = await crud.get_geofence_state(session, fence.id, child.id)

        if state is None:
            # First time recording status, initialize without false alert
            await crud.update_geofence_state(session, fence.id, child.id, is_inside_now)
            continue

        # Check for state transitions
        if not state.is_inside and is_inside_now:
            # Child ENTERED kerakli joy
            event_type = "entered"
            message_text = (
                f"✅ **KERAKLI JOYGA YETIB BORDI**\n\n"
                f"👤 Farzandingiz: **{child_name}**\n"
                f"📍 Joy: **{fence.name}**\n"
                f"🕒 Vaqt: **{now_str}**\n"
                f"📏 Masofa: markazdan {int(dist)} m"
            )
            events.append({
                "fence_id": fence.id,
                "fence_name": fence.name,
                "event": event_type,
                "distance": dist
            })
            await crud.update_geofence_state(session, fence.id, child.id, True)

            # Send Telegram notification to all linked parents
            notify_ids = {p.id for p in parents}
            if fence.parent_id:
                notify_ids.add(fence.parent_id)

            if bot and notify_ids:
                for pid in notify_ids:
                    try:
                        await bot.send_message(
                            chat_id=pid,
                            text=message_text,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        print(f"[Geofence Alert Error] Failed to notify parent {pid}: {e}")

        elif state.is_inside and not is_inside_now:
            # Child EXITED kerakli joy
            event_type = "exited"
            message_text = (
                f"⚠️ **KERAKLI JOYDAN CHIQDI**\n\n"
                f"👤 Farzandingiz: **{child_name}**\n"
                f"📍 Joy: **{fence.name}**\n"
                f"🕒 Vaqt: **{now_str}**\n"
                f"📏 Masofa: markazdan {int(dist)} m"
            )
            events.append({
                "fence_id": fence.id,
                "fence_name": fence.name,
                "event": event_type,
                "distance": dist
            })
            await crud.update_geofence_state(session, fence.id, child.id, False)

            # Send Telegram notification to all linked parents
            notify_ids = {p.id for p in parents}
            if fence.parent_id:
                notify_ids.add(fence.parent_id)

            if bot and notify_ids:
                for pid in notify_ids:
                    try:
                        await bot.send_message(
                            chat_id=pid,
                            text=message_text,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        print(f"[Geofence Alert Error] Failed to notify parent {pid}: {e}")

    return events
