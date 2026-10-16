from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
import db.crud as crud

router = APIRouter(prefix="/api/v1/webapp", tags=["WebApp"])


class CreateGeofenceRequest(BaseModel):
    parent_id: int
    name: str = Field(..., min_length=1, max_length=100)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    radius_meters: float = Field(200.0, ge=50.0, le=5000.0)
    child_id: Optional[int] = None


@router.get("/parent/{parent_id}/children")
async def get_parent_children(parent_id: int, session: AsyncSession = Depends(get_db)):
    """Fetch all linked children with their latest location and telemetry state."""
    children = await crud.get_children_for_parent(session, parent_id)
    result = []

    for child in children:
        latest = await crud.get_latest_location(session, child.id)
        result.append({
            "id": child.id,
            "full_name": child.full_name or "Farzand",
            "username": child.username,
            "virtual_balance": child.virtual_balance,
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
    """Fetch single child latest telemetry and status."""
    child = await crud.get_user(session, child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child not found")

    latest = await crud.get_latest_location(session, child_id)
    return {
        "status": "success",
        "child": {
            "id": child.id,
            "full_name": child.full_name,
            "username": child.username,
            "virtual_balance": child.virtual_balance,
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


@router.get("/child/{child_id}/tasks")
async def get_child_tasks(child_id: int, session: AsyncSession = Depends(get_db)):
    """Fetch child tasks list for WebApp display."""
    tasks = await crud.get_tasks_for_child(session, child_id)
    return {
        "status": "success",
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "reward_coins": t.reward_coins,
                "status": t.status,
                "created_at": t.created_at.isoformat() if t.created_at else None
            }
            for t in tasks
        ]
    }
