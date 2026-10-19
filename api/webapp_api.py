import logging
import uuid
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.types import FSInputFile

from db.database import get_db
import db.crud as crud
from api import telemetry
from bot.keyboards import get_child_task_action_keyboard, get_task_approval_keyboard

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webapp", tags=["WebApp"])


class CreateGeofenceRequest(BaseModel):
    parent_id: int
    name: str = Field(..., min_length=1, max_length=100)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    radius_meters: float = Field(200.0, ge=50.0, le=5000.0)
    child_id: Optional[int] = None


class AuthCodeRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)


@router.post("/auth/code")
async def verify_auth_code_endpoint(payload: AuthCodeRequest, session: AsyncSession = Depends(get_db)):
    """Verify web access code or pairing code and return authenticated user context."""
    code = payload.code.strip()
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
            "role": "child",
            "virtual_balance": user.virtual_balance,
            "virtual_money_som": money_som,
            "coin_rate_coins": rate_coins,
            "coin_rate_som": rate_som,
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
            "full_name": parent.full_name or "Ota-ona"
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
    uploads_dir = Path(__file__).resolve().parent.parent / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename or "").suffix.lower()
    unique_filename = f"{uuid.uuid4().hex}{ext if ext else '.jpg'}"
    target_path = uploads_dir / unique_filename

    contents = await file.read()
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
async def get_parent_tasks_endpoint(parent_id: int, session: AsyncSession = Depends(get_db)):
    """Fetch all tasks assigned by parent for WebApp display."""
    tasks = await crud.get_tasks_for_parent(session, parent_id)
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
            caption = (
                f"🔔 **Topshiriq bajarildi! Farzandingiz hisobot rasmini yubordi:**\n\n"
                f"👤 Farzand: **{child_name}**\n"
                f"📌 Topshiriq: **{task.title}**\n"
                f"💰 Mukofot: **{task.reward_coins:,}** tanga\n\n"
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
                    caption=caption,
                    reply_markup=markup,
                    parse_mode="Markdown"
                )
            except Exception:
                await bot.send_message(
                    chat_id=task.parent_id,
                    text=f"{caption}\n\n📷 Rasm hisoboti: {payload.proof_photo_url}",
                    reply_markup=markup,
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"Failed to forward completed task to parent Telegram: {e}")

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
            await telemetry.telegram_bot.send_message(
                chat_id=task.child_id,
                text=(
                    f"🎉🎉 **TABRIKLAYMIZ!** 🎉🎉\n\n"
                    f"Ota-onangiz **'{task.title}'** vazifangizni tasdiqladi!\n"
                    f"💰 Balansingizga: **+{task.reward_coins:,} tanga** qo'shildi!\n"
                    f"Hozirgi umumiy balansingiz: **{new_balance:,} tanga**."
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify child of approved task: {e}")

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
            await telemetry.telegram_bot.send_message(
                chat_id=task.child_id,
                text=f"❌ **'{task.title}'** vazifasi ota-onangiz tomonidan rad etildi. Iltimos, vazifani to'liq bajarib, qayta topshiring.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify child of rejected task: {e}")

    await session.commit()
    return {"status": "success", "task_id": task_id, "status": "rejected"}


@router.post("/child/{child_id}/sos")
async def child_sos_endpoint(child_id: int, session: AsyncSession = Depends(get_db)):
    """Trigger SOS emergency alert from child WebApp and notify all linked parents."""
    child = await crud.get_user(session, child_id)
    child_name = child.full_name if child else f"Farzand #{child_id}"
    parents = await crud.get_parents_for_child(session, child_id)

    if telemetry.telegram_bot and parents:
        for p in parents:
            try:
                await telemetry.telegram_bot.send_message(
                    chat_id=p.id,
                    text=f"🚨 **SHOSHILINCH SOS SIGNAL!**\n\nFarzandingiz **{child_name}** WebApp orqali SOS xavf signalini yoqdi!\nIltimos, zudlik bilan farzandingiz bilan bog'laning yoki xaritada lokatsiyasini ko'ring.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to notify parent {p.id} of SOS: {e}")

    return {"status": "success", "child_id": child_id, "notified_parents": len(parents)}


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


