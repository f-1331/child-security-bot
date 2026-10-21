import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File, WebSocket, WebSocketDisconnect
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
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webapp", tags=["WebApp"])


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


class ChildLocationUpdate(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    accuracy: Optional[float] = None
    speed: Optional[float] = None
    battery_level: Optional[int] = None
    is_charging: Optional[bool] = False
    sound_mode: Optional[str] = "normal"


class CreateGeofenceRequest(BaseModel):
    parent_id: int
    name: str = Field(..., min_length=1, max_length=100)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    radius_meters: float = Field(200.0, ge=50.0, le=5000.0)
    child_id: Optional[int] = None


class AuthCodeRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)


class ChildSOSRequest(BaseModel):
    is_sos_active: bool = True
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accuracy: Optional[float] = None
    battery_level: Optional[int] = None


@router.post("/auth/code")
async def verify_auth_code_endpoint(payload: AuthCodeRequest, session: AsyncSession = Depends(get_db)):
    """Verify web access code or pairing code and return authenticated user context."""
    code = payload.code.strip().replace(" ", "").replace("-", "")
    user = await crud.get_user_by_web_access_code(session, code)
    if not user:
        raise HTTPException(
            status_code=404,
            detail="Kiritilgan kod noto'g'ri yoki mavjud emas. Iltimos, Telegram bot orqali kodni tekshiring."
        )

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
                    "recorded_at": latest.recorded_at.isoformat() if latest and latest.recorded_at else None,
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
                "web_access_code": user.web_access_code or code
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
            "web_access_code": user.web_access_code or code,
            "location": {
                "latitude": latest.latitude if latest else None,
                "longitude": latest.longitude if latest else None,
                "battery_level": latest.battery_level if latest else None,
                "is_charging": latest.is_charging if latest else False,
                "sound_mode": latest.sound_mode if latest else "normal",
                "recorded_at": latest.recorded_at.isoformat() if latest and latest.recorded_at else None,
            } if latest else None
        },
        "parent": {
            "id": parent.id,
            "full_name": parent.full_name or "Ota-ona",
            "phone_number": parent.phone_number if parent else None
        } if parent else None
    }


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
        "som_per_coin": rate_som / rate_coins
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
                        f"📢 **Tangalar kursi yangilandi!**\n\n"
                        f"📌 Ota-onangiz belgilagan yangi kurs:\n"
                        f"💰 **{payload.coins:,} tanga = {payload.som:,} so'm**\n"
                        f"(1 tanga = {payload.som / payload.coins:,.1f} so'm)\n\n"
                        f"💵 Sizning hozirgi pulingiz: **{child_money:,} so'm** ({child.virtual_balance:,} tanga)"
                    ),
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"Failed to notify children about coin rate update: {e}")

    return {
        "status": "success",
        "coins": payload.coins,
        "som": payload.som,
        "som_per_coin": payload.som / payload.coins
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
                "recorded_at": latest.recorded_at.isoformat() if latest and latest.recorded_at else None,
            } if latest else None
        })

    return {"status": "success", "children": result}


@router.get("/child/{child_id}/status")
async def get_child_status(child_id: int, session: AsyncSession = Depends(get_db)):
    """Fetch single child latest telemetry, balance, and real money equivalent."""
    child = await crud.get_user(session, child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child not found")

    rate_coins, rate_som = await crud.get_coin_rate_for_user(session, child_id)
    money_som = crud.calculate_som_from_coins(child.virtual_balance, rate_coins, rate_som)
    latest = await crud.get_latest_location(session, child_id)
    parents = await crud.get_parents_for_child(session, child_id)
    parent_phone = parents[0].phone_number if parents and parents[0].phone_number else None
    parent_name = parents[0].full_name if parents and parents[0].full_name else None

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
            "location": {
                "latitude": latest.latitude if latest else None,
                "longitude": latest.longitude if latest else None,
                "battery_level": latest.battery_level if latest else None,
                "is_charging": latest.is_charging if latest else False,
                "sound_mode": latest.sound_mode if latest else "normal",
                "recorded_at": latest.recorded_at.isoformat() if latest and latest.recorded_at else None,
            } if latest else None
        }
    }


@router.get("/child/{child_id}/history")
async def get_child_history(
    child_id: int,
    limit: int = Query(150, ge=10, le=500),
    session: AsyncSession = Depends(get_db)
):
    """Fetch recent location history points for movement polyline visualization."""
    history = await crud.get_location_history_today(session, child_id, limit=limit)
    points = [
        {
            "id": h.id,
            "latitude": h.latitude,
            "longitude": h.longitude,
            "battery_level": h.battery_level,
            "is_charging": h.is_charging,
            "recorded_at": h.recorded_at.isoformat() if h.recorded_at else None
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
async def upload_file_endpoint(file: UploadFile = File(...)):
    """Upload media file (photo or video) for task assignment or proof."""
    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".avi", ".webm", ".mkv", ".3gp"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Faqat rasm yoki video fayllari qabul qilinadi")

    uploads_dir = Path(__file__).resolve().parent.parent / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    contents = await file.read()
    if len(contents) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Fayl hajmi 25 MB dan oshmasligi kerak")

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
            caption = (
                f"🎯 **SIZGA YANGI VAZIFA BERILDI!**\n\n"
                f"📌 **Vazifa:** {task.title}\n"
                + (f"📝 **Tavsif:** {task.description}\n" if task.description else "")
                + f"💰 **Mukofot:** +{task.reward_coins:,} tanga!\n\n"
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
                    await bot.send_photo(chat_id=task.child_id, photo=target_media, caption=caption, reply_markup=markup, parse_mode="Markdown")
                elif task.media_type == "video":
                    await bot.send_video(chat_id=task.child_id, video=target_media, caption=caption, reply_markup=markup, parse_mode="Markdown")
                else:
                    await bot.send_message(chat_id=task.child_id, text=caption, reply_markup=markup, parse_mode="Markdown")
            else:
                await bot.send_message(chat_id=task.child_id, text=caption, reply_markup=markup, parse_mode="Markdown")
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
                    f"👁 **Bolangiz bu habarni ko'rdi**\n\n"
                    f"📌 Topshiriq: **{task.title}**\n"
                    f"👤 Farzand: **{child_name}**"
                ),
                parse_mode="Markdown"
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
                    f"⏰ **Bolangiz {pt_clean}{suffix} bu topshiriqni bajarishga qaror qildi**\n\n"
                    f"📌 Topshiriq: **{task.title}**\n"
                    f"👤 Farzand: **{child_name}**"
                ),
                parse_mode="Markdown"
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

            target_photo = local_path if local_path else payload.proof_photo_url
            try:
                await bot.send_photo(
                    chat_id=task.parent_id,
                    photo=target_photo,
                    caption=caption_html,
                    reply_markup=markup,
                    parse_mode="HTML"
                )
            except Exception:
                raw_cap = (
                    f"🔔 Topshiriq bajarildi! Farzandingiz hisobot rasmini yubordi:\n\n"
                    f"👤 Farzand: {child_name}\n"
                    f"📌 Topshiriq: {task.title}\n"
                    f"💰 Mukofot: {task.reward_coins:,} tanga\n\n"
                    f"Topshiriq to'g'ri bajarilgan bo'lsa, tasdiqlang:"
                )
                await bot.send_message(
                    chat_id=task.parent_id,
                    text=f"{raw_cap}\n\n📷 Rasm hisoboti: {payload.proof_photo_url}",
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
async def approve_task_endpoint(task_id: int, session: AsyncSession = Depends(get_db)):
    """Approve a task directly from WebApp and notify child."""
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
async def reject_task_endpoint(task_id: int, session: AsyncSession = Depends(get_db)):
    """Reject a task directly from WebApp and notify child."""
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

    if telemetry.telegram_bot and parents:
        now_str = datetime.now().strftime("%d.%m.%Y, %H:%M:%S")
        battery_info = f"{battery_lvl}%" if battery_lvl is not None else "Noma'lum"
        loc_coords = f"{lat}, {lon}" if (lat is not None and lon is not None) else "Mavjud emas"
        maps_link = (
            f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
            if (lat is not None and lon is not None) else None
        )

        parent_alert = (
            f"🚨🚨🚨 **FAVQULODDA VAZIYAT (SOS)!** 🚨🚨🚨\n\n"
            f"Farzandingiz **{child_name}** WebApp orqali xavf signalini yoqdi!\n\n"
            f"🕒 Vaqt: **{now_str}**\n"
            f"🔋 Batareya: **{battery_info}**\n"
            + (f"📍 Koordinata: `{loc_coords}`\n🗺 Xaritada: [Google Maps orqali ochish]({maps_link})\n\n" if maps_link else "\n")
            + f"⚠️ **DIQQAT:** Farzandingiz bilan zudlik bilan bog'laning yoki xaritada tekshiring!"
        )

        for p in parents:
            try:
                await telemetry.telegram_bot.send_message(
                    chat_id=p.id,
                    text=parent_alert,
                    reply_markup=get_sos_alert_parent_keyboard(child_id, lat, lon),
                    parse_mode="Markdown"
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
    child = await crud.get_user(session, child_id)
    child_name = child.full_name if child else f"Farzand #{child_id}"
    parents = await crud.get_parents_for_child(session, child_id)
    await session.commit()

    if telemetry.telegram_bot:
        if parents:
            for p in parents:
                try:
                    await telemetry.telegram_bot.send_message(
                        chat_id=p.id,
                        text=f"✅ **XAVF BARTARAF ETILDI!**\n\nFarzandingiz **{child_name}** SOS xavf signalini to'xtatdi. Hammasi joyida va xavfsiz ekanligi tasdiqlandi.",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify parent {p.id} of SOS cancellation: {e}")

        if child:
            try:
                await telemetry.telegram_bot.send_message(
                    chat_id=child.id,
                    text="✅ SOS xavf signali to'xtatildi. Hammasi joyida ekanligidan xursandmiz!",
                    parse_mode="Markdown"
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
                await telemetry.telegram_bot.send_message(
                    chat_id=p.id,
                    text=f"✅ **Manzilga yetib borildi!**\n\nFarzandingiz **{child_name}**: *«Men manzilga yetib keldim»* deb xabar berdi.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to notify parent {p.id} of checkin: {e}")

    return {"status": "success", "child_id": child_id, "notified_parents": len(parents)}


@router.post("/child/{child_id}/siren")
async def parent_siren_endpoint(child_id: int, session: AsyncSession = Depends(get_db)):
    """Parent triggers remote siren / sound ping to child's phone."""
    if telemetry.telegram_bot:
        try:
            await telemetry.telegram_bot.send_message(
                chat_id=child_id,
                text="🔔 **DIQQAT: Ota-onangiz telefoningizga qidiruv/ogohlantirish signali yubordi!**",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send siren alert to child {child_id}: {e}")

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
    """Receive live telemetry from child's watchPosition and save to database."""
    log = await crud.log_location(
        session=session,
        child_id=child_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        accuracy=payload.accuracy,
        battery_level=payload.battery_level,
        is_charging=payload.is_charging or False,
        sound_mode=payload.sound_mode or "normal",
        source="web_watch_position"
    )

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
                    alert_msg = (
                        f"🪫 **OGOHLANTIRISH: QUVVAT KAM QOLDI!**\n\n"
                        f"👤 Farzandingiz: **{child_name}**\n"
                        f"🔋 Batareya darajasi: **{payload.battery_level}%**\n"
                        f"⚡ Holat: Quvvatlanmayapti\n\n"
                        f"Iltimos, farzandingiz bilan bog'lanib, telefonini quvvatga qo'yishini eslating."
                    )
                    for p in parents:
                        try:
                            await telemetry.telegram_bot.send_message(
                                chat_id=p.id,
                                text=alert_msg,
                                parse_mode="Markdown"
                            )
                        except Exception:
                            pass
            except Exception as batt_err:
                logger.error(f"Battery alert check error: {batt_err}")

        # 3. Active SOS push
        if child.is_sos_active:
            parents = await crud.get_parents_for_child(session, child_id)
            for p in parents:
                try:
                    await telemetry.telegram_bot.send_location(
                        chat_id=p.id,
                        latitude=payload.latitude,
                        longitude=payload.longitude
                    )
                except Exception:
                    pass

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
        "recorded_at": log.recorded_at.isoformat() if log.recorded_at else datetime.now().isoformat()
    }
    for p in parents:
        await ws_manager.send_to_user(p.id, telemetry_data)

    return {
        "status": "success",
        "child_id": child_id,
        "recorded_at": log.recorded_at.isoformat() if log.recorded_at else None
    }


@router.post("/child/{child_id}/silent-check")
async def silent_check_endpoint(
    child_id: int,
    session: AsyncSession = Depends(get_db)
):
    """Silent Check ('Passiv monitoring'):
    Fetch latest live coordinates and telemetry for parent.
    Strictly NO UI alert/notification is sent to child!
    """
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
            "recorded_at": latest.recorded_at.isoformat() if latest and latest.recorded_at else None
        } if latest else None,
        "notification_sent_to_child": False
    }


@router.post("/child/{child_id}/active-check")
async def active_check_endpoint(
    child_id: int,
    session: AsyncSession = Depends(get_db)
):
    """Active Check ('Faol so'rov'):
    Fetch latest coordinates and send 'Ota-onangiz joylashuvingizni tekshirdi' alert to child screen.
    """
    latest = await crud.get_latest_location(session, child_id)
    child = await crud.get_user(session, child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Farzand topilmadi")

    now_time = datetime.now().strftime("%H:%M")
    alert_msg = {
        "type": "active_check",
        "title": "Joylashuv tekshiruvi 📍",
        "message": "Ota-onangiz joylashuvingizni tekshirdi 📍",
        "time": now_time,
        "timestamp": datetime.now().isoformat()
    }

    # 1. Dispatch through real-time WebSocket
    await ws_manager.send_to_user(child_id, alert_msg)

    # 2. Add to pending notifications queue (for HTTP polling fallback)
    pending_child_notifications[child_id].append(alert_msg)

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
            "recorded_at": latest.recorded_at.isoformat() if latest and latest.recorded_at else None
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

    bot_username = "ChildSecurityBot"
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

    bot_username = "ChildSecurityBot"
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




