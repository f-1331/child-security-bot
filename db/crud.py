import math
import random
import string
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple
from sqlalchemy import desc, select, and_, or_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import (
    User,
    ParentChildLink,
    LocationLog,
    Geofence,
    GeofenceState,
    Task,
    BatteryAlertLog,
    AdminUser,
    ChildDevicePolicy,
    AppUsageReport,
)
import json
import hashlib


async def get_user(session: AsyncSession, user_id: int) -> Optional[User]:
    """Retrieve user by Telegram ID."""
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_or_create_user(
    session: AsyncSession,
    user_id: int,
    role: str = "unassigned",
    full_name: Optional[str] = None,
    username: Optional[str] = None
) -> Tuple[User, bool]:
    """Get existing user or create a new one."""
    user = await get_user(session, user_id)
    if user:
        # Update user profile info if changed
        if full_name and user.full_name != full_name:
            user.full_name = full_name
        if username and user.username != username:
            user.username = username
        return user, False

    user = User(
        id=user_id,
        role=role,
        full_name=full_name,
        username=username,
    )
    session.add(user)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return user, True


async def update_user_role(session: AsyncSession, user_id: int, role: str) -> Optional[User]:
    """Update role for user."""
    user = await get_user(session, user_id)
    if user:
        user.role = role
        await session.commit()
    return user


async def generate_pairing_code(session: AsyncSession, parent_id: int) -> str:
    """Generate or retrieve a unique 6-character permanent pairing code for a parent.
    Also synchronizes web_access_code so one code controls the entire family.
    """
    user = await get_user(session, parent_id)
    if not user:
        user, _ = await get_or_create_user(session, parent_id, role="parent")

    if user.pairing_code:
        user.web_access_code = user.pairing_code
        await session.commit()
        return user.pairing_code

    if user.web_access_code:
        user.pairing_code = user.web_access_code
        user.pairing_code_created_at = datetime.now(timezone.utc)
        await session.commit()
        return user.web_access_code

    # Generate permanent 6-digit numeric code
    code = "".join(random.choices(string.digits, k=6))
    user.pairing_code = code
    user.web_access_code = code
    user.pairing_code_created_at = datetime.now(timezone.utc)
    await session.commit()
    return code


async def get_parent_by_pairing_code(session: AsyncSession, code: str) -> Optional[User]:
    """Find parent by their pairing code or web access code."""
    if not code:
        return None
    clean_code = str(code).strip().replace(" ", "")
    result = await session.execute(
        select(User).where(
            (User.pairing_code == clean_code) | (User.web_access_code == clean_code),
            User.role == "parent"
        )
    )
    return result.scalar_one_or_none()


async def generate_web_access_code(session: AsyncSession, user_id: int) -> str:
    """Generate or retrieve a permanent 6-digit access code for website login.
    Parents get their unified family code (pairing_code).
    Children get their OWN DISTINCT personal access code so they can log into their child account.
    """
    user = await get_user(session, user_id)
    if not user:
        user, _ = await get_or_create_user(session, user_id)

    if user.role == "parent":
        return await generate_pairing_code(session, user_id)

    # For Child: Check if child already has their distinct code
    if user.web_access_code:
        parents = await get_parents_for_child(session, user_id)
        parent_codes = {p.pairing_code for p in parents if p.pairing_code}
        if user.web_access_code not in parent_codes:
            return user.web_access_code

    # Generate unique 6-digit code specifically for this child
    for _ in range(20):
        code = "".join(random.choices(string.digits, k=6))
        existing = await session.execute(
            select(User).where((User.web_access_code == code) | (User.pairing_code == code))
        )
        if not existing.scalars().first():
            user.web_access_code = code
            await session.commit()
            return code

    code = str(int(datetime.now().timestamp()))[-6:]
    user.web_access_code = code
    await session.commit()
    return code


async def get_user_by_web_access_code(session: AsyncSession, code: str) -> Optional[User]:
    """Find user by web access code (or pairing code, or telegram ID fallback).
    - If code belongs to a Child, returns the Child (child account opens).
    - If code belongs to a Parent, returns the Parent (all children load together).
    """
    code_clean = code.strip().replace(" ", "").replace("-", "")

    # 1. Match directly on web_access_code
    result = await session.execute(
        select(User).where(User.web_access_code == code_clean)
    )
    user = result.scalars().first()
    if user:
        return user

    # 2. Match on pairing_code (e.g. parent's pairing code)
    result = await session.execute(
        select(User).where(User.pairing_code == code_clean)
    )
    user = result.scalars().first()
    if user:
        return user

    # Telegram ID fallback removed for defensive security (requires cryptographically signed initData or valid 6-digit access code)

    return None


async def link_co_parent(session: AsyncSession, primary_parent_id: int, new_parent_id: int) -> List[User]:
    """Link a second parent (e.g. mother invites father or vice-versa) to all existing children."""
    new_parent = await get_user(session, new_parent_id)
    if not new_parent:
        new_parent, _ = await get_or_create_user(session, new_parent_id, role="parent")
    else:
        new_parent.role = "parent"

    await generate_pairing_code(session, new_parent_id)

    children = await get_children_for_parent(session, primary_parent_id)
    for child in children:
        await link_parent_child(session, new_parent_id, child.id, nickname=child.full_name)

    await session.flush()
    return children


async def link_child_to_parent_by_invite(session: AsyncSession, child_id: int, parent_id: int) -> Tuple[User, bool]:
    """Child invites parent: link parent to child and ensure parent role."""
    parent = await get_user(session, parent_id)
    if not parent:
        parent, _ = await get_or_create_user(session, parent_id, role="parent")
    else:
        if parent.role != "child":
            parent.role = "parent"

    child = await get_user(session, child_id)
    link, is_new = await link_parent_child(session, parent_id, child_id, nickname=child.full_name if child else None)
    await session.flush()
    return parent, is_new


async def update_coin_rate(
    session: AsyncSession,
    user_id: int,
    coins: int,
    som: int
) -> Optional[User]:
    """Update coin-to-som rate settings for parent."""
    user = await get_user(session, user_id)
    if user:
        user.coin_rate_coins = max(1, coins)
        user.coin_rate_som = max(1, som)
        await session.flush()
    return user


async def get_coin_rate_for_user(session: AsyncSession, user_id: int) -> tuple[int, int]:
    """
    Get coin rate (coins, som).
    If user is child, inherits rate from parent.
    """
    user = await get_user(session, user_id)
    if not user:
        return (1000, 10000)

    if user.role == "parent":
        return (user.coin_rate_coins or 1000, user.coin_rate_som or 10000)

    # If child, lookup linked parent
    result = await session.execute(
        select(User).join(
            ParentChildLink, ParentChildLink.parent_id == User.id
        ).where(ParentChildLink.child_id == user_id)
    )
    parent = result.scalar_one_or_none()
    if parent and parent.coin_rate_coins and parent.coin_rate_som:
        return (parent.coin_rate_coins, parent.coin_rate_som)

    return (1000, 10000)

get_user_coin_rate = get_coin_rate_for_user  # Alias for backward compatibility


def calculate_som_from_coins(coins: int, rate_coins: int = 1000, rate_som: int = 10000) -> int:
    """Calculate equivalent real money in so'm for given coins."""
    if rate_coins <= 0:
        rate_coins = 1000
    return int(coins * (rate_som / rate_coins))


async def link_parent_child(
    session: AsyncSession,
    parent_id: int,
    child_id: int,
    nickname: Optional[str] = None
) -> Tuple[Optional[ParentChildLink], bool]:
    """Link parent and child together, ensuring child role and nickname."""
    if parent_id == child_id:
        return None, False

    child_user = await get_user(session, child_id)
    if child_user:
        child_user.role = "child"
        if nickname and (not child_user.full_name or child_user.full_name == "Farzand"):
            child_user.full_name = nickname
        await session.flush()

    result = await session.execute(
        select(ParentChildLink).where(
            ParentChildLink.parent_id == parent_id,
            ParentChildLink.child_id == child_id
        )
    )
    link = result.scalar_one_or_none()
    if link:
        if nickname and link.nickname != nickname:
            link.nickname = nickname
            await session.flush()
        return link, False

    link = ParentChildLink(
        parent_id=parent_id,
        child_id=child_id,
        nickname=nickname
    )
    session.add(link)
    await session.flush()
    return link, True


async def get_parent_child_link(session: AsyncSession, parent_id: int, child_id: int) -> Optional[ParentChildLink]:
    """Retrieve link between a parent and child."""
    result = await session.execute(
        select(ParentChildLink).where(
            ParentChildLink.parent_id == parent_id,
            ParentChildLink.child_id == child_id
        )
    )
    return result.scalar_one_or_none()


async def get_children_for_parent(session: AsyncSession, parent_id: int) -> List[User]:
    """Fetch all linked children for a parent."""
    result = await session.execute(
        select(User)
        .join(ParentChildLink, ParentChildLink.child_id == User.id)
        .where(ParentChildLink.parent_id == parent_id)
    )
    return list(result.scalars().all())


async def get_parents_for_child(session: AsyncSession, child_id: int) -> List[User]:
    """Fetch all linked parents for a child."""
    result = await session.execute(
        select(User)
        .join(ParentChildLink, ParentChildLink.parent_id == User.id)
        .where(ParentChildLink.child_id == child_id)
    )
    return list(result.scalars().all())


async def delete_child_profile(session: AsyncSession, parent_id: int, child_id: int) -> bool:
    """Unlink and delete a child profile from parent's account and database."""
    # 1. Delete link
    result = await session.execute(
        select(ParentChildLink).where(
            ParentChildLink.parent_id == parent_id,
            ParentChildLink.child_id == child_id
        )
    )
    link = result.scalar_one_or_none()
    if not link:
        return False
    await session.delete(link)
    await session.flush()

    # 2. Check if child is linked to any other parent
    other_links = await session.execute(
        select(ParentChildLink).where(ParentChildLink.child_id == child_id)
    )
    if not other_links.scalars().first():
        child_user = await get_user(session, child_id)
        if child_user:
            await session.execute(delete(LocationLog).where(LocationLog.child_id == child_id))
            await session.execute(delete(Task).where(Task.child_id == child_id))
            await session.execute(delete(BatteryAlertLog).where(BatteryAlertLog.child_id == child_id))
            await session.execute(delete(GeofenceState).where(GeofenceState.child_id == child_id))
            await session.execute(delete(Geofence).where(Geofence.child_id == child_id))
            await session.delete(child_user)
            await session.flush()

    return True


async def log_location(
    session: AsyncSession,
    child_id: int,
    latitude: float,
    longitude: float,
    accuracy: Optional[float] = None,
    battery_level: Optional[int] = None,
    is_charging: bool = False,
    sound_mode: str = "normal",
    source: str = "telegram_live",
    speed: Optional[float] = None
) -> LocationLog:
    """Record a location telemetry entry."""
    entry = LocationLog(
        child_id=child_id,
        latitude=latitude,
        longitude=longitude,
        accuracy=accuracy,
        speed=speed,
        battery_level=battery_level,
        is_charging=is_charging,
        sound_mode=sound_mode,
        source=source
    )
    session.add(entry)
    await session.flush()
    return entry


async def batch_log_locations(
    session: AsyncSession,
    child_id: int,
    points: List[dict]
) -> int:
    """Batch insert offline locations with timestamp ordering and deduplication."""
    if not points:
        return 0

    inserted_count = 0
    for p in points:
        lat = p.get("latitude")
        lon = p.get("longitude")
        if lat is None or lon is None:
            continue
        try:
            lat = float(lat)
            lon = float(lon)
        except (ValueError, TypeError):
            continue

        recorded_at = None
        epoch_ms = p.get("recorded_at_epoch_ms")
        if epoch_ms:
            try:
                recorded_at = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
            except Exception:
                pass
        ts_val = p.get("recorded_at") or p.get("timestamp")
        if not recorded_at and ts_val:
            try:
                s = str(ts_val).strip()
                if not s.endswith("Z") and not ("+" in s):
                    s += "Z"
                recorded_at = datetime.fromisoformat(s.replace("Z", "+00:00"))
            except Exception:
                pass
        if not recorded_at:
            recorded_at = datetime.now(timezone.utc)

        entry = LocationLog(
            child_id=child_id,
            latitude=lat,
            longitude=lon,
            accuracy=float(p.get("accuracy", 10.0)) if p.get("accuracy") is not None else None,
            speed=float(p.get("speed", p.get("speed_kmh", 0))) if (p.get("speed") is not None or p.get("speed_kmh") is not None) else 0.0,
            battery_level=int(p.get("battery_level", 100)) if p.get("battery_level") is not None else None,
            is_charging=bool(p.get("is_charging", False)),
            sound_mode=str(p.get("sound_mode", "normal")),
            source="offline_batch_sync",
            is_offline_cached=True,
            recorded_at=recorded_at
        )
        session.add(entry)
        inserted_count += 1

    if inserted_count > 0:
        await session.commit()
    return inserted_count


async def get_latest_location(session: AsyncSession, child_id: int) -> Optional[LocationLog]:
    """Fetch the most recent location log for a child."""
    result = await session.execute(
        select(LocationLog)
        .where(LocationLog.child_id == child_id)
        .order_by(desc(LocationLog.recorded_at), desc(LocationLog.id))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_location_history_today(
    session: AsyncSession,
    child_id: int,
    limit: int = 500
) -> List[LocationLog]:
    """Fetch location history for today (last 24 hours), returning most recent points chronologically."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await session.execute(
        select(LocationLog)
        .where(
            LocationLog.child_id == child_id,
            LocationLog.recorded_at >= cutoff
        )
        .order_by(LocationLog.recorded_at.desc(), LocationLog.id.desc())
        .limit(limit)
    )
    logs = list(result.scalars().all())
    logs.reverse()
    return logs


async def create_geofence(
    session: AsyncSession,
    parent_id: int,
    name: str,
    latitude: float,
    longitude: float,
    radius_meters: float = 200.0,
    child_id: Optional[int] = None
) -> Geofence:
    """Create a new safe zone (geofence)."""
    fence = Geofence(
        parent_id=parent_id,
        child_id=child_id,
        name=name.strip(),
        latitude=latitude,
        longitude=longitude,
        radius_meters=radius_meters,
        is_active=True
    )
    session.add(fence)
    await session.flush()
    return fence


async def get_geofences_for_parent(session: AsyncSession, parent_id: int) -> List[Geofence]:
    """Get all geofences created by a parent."""
    result = await session.execute(
        select(Geofence)
        .where(
            Geofence.parent_id == parent_id,
            or_(Geofence.is_active.is_(True), Geofence.is_active.is_(None))
        )
        .order_by(Geofence.created_at.desc())
    )
    return list(result.scalars().all())


async def get_active_geofences_for_child(session: AsyncSession, child_id: int) -> List[Geofence]:
    """Get all safe zones applicable to a child (from their parents and direct assignments)."""
    parent_ids = set()

    # 1. Linked parents via ParentChildLink
    parents = await get_parents_for_child(session, child_id)
    for p in parents:
        parent_ids.add(p.id)

    # 2. Check ParentChildLink where child_id matches
    link_res = await session.execute(
        select(ParentChildLink.parent_id).where(ParentChildLink.child_id == child_id)
    )
    for pid in link_res.scalars().all():
        if pid:
            parent_ids.add(pid)

    # 3. Fallback: Include child_id itself (handles self-testing or role switch in bot/webapp)
    parent_ids.add(child_id)

    conditions = [
        Geofence.child_id == child_id
    ]
    if parent_ids:
        conditions.append(
            and_(
                Geofence.parent_id.in_(list(parent_ids)),
                or_(
                    Geofence.child_id.is_(None),
                    Geofence.child_id == child_id
                )
            )
        )

    active_condition = or_(Geofence.is_active.is_(True), Geofence.is_active.is_(None))

    result = await session.execute(
        select(Geofence)
        .where(
            active_condition,
            or_(*conditions)
        )
        .order_by(Geofence.created_at.desc())
    )

    unique_fences = []
    seen = set()
    for f in result.scalars().all():
        if f.id not in seen:
            seen.add(f.id)
            unique_fences.append(f)
    return unique_fences


# Backward-compatible alias used in report generators
get_geofences_for_child = get_active_geofences_for_child


async def get_geofences_for_user(session: AsyncSession, user_id: int) -> List[Geofence]:
    """Get geofences for a user ID, supporting both parent and child roles."""
    user = await get_user(session, user_id)
    if user and user.role == 'parent':
        return await get_geofences_for_parent(session, user_id)
    elif user and user.role == 'child':
        return await get_active_geofences_for_child(session, user_id)
    else:
        # Fallback: check parent geofences first, then child geofences
        fences = await get_geofences_for_parent(session, user_id)
        if fences:
            return fences
        return await get_active_geofences_for_child(session, user_id)


async def get_geofence(session: AsyncSession, geofence_id: int) -> Optional[Geofence]:
    """Retrieve a single geofence by ID."""
    result = await session.execute(
        select(Geofence).where(Geofence.id == geofence_id)
    )
    return result.scalar_one_or_none()


async def delete_geofence(session: AsyncSession, geofence_id: int, parent_id: int) -> bool:
    """Delete a geofence by ID if owned by parent, cleaning up related states."""
    # Remove associated geofence tracking states first
    await session.execute(
        delete(GeofenceState).where(GeofenceState.geofence_id == geofence_id)
    )
    result = await session.execute(
        delete(Geofence).where(
            Geofence.id == geofence_id,
            Geofence.parent_id == parent_id
        )
    )
    await session.flush()
    return result.rowcount > 0


async def get_geofence_state(session: AsyncSession, geofence_id: int, child_id: int) -> Optional[GeofenceState]:
    """Get current inside/outside status of child relative to geofence."""
    result = await session.execute(
        select(GeofenceState).where(
            GeofenceState.geofence_id == geofence_id,
            GeofenceState.child_id == child_id
        )
    )
    return result.scalar_one_or_none()


async def update_geofence_state(
    session: AsyncSession,
    geofence_id: int,
    child_id: int,
    is_inside: bool
) -> GeofenceState:
    """Update or initialize inside/outside state."""
    state = await get_geofence_state(session, geofence_id, child_id)
    now = datetime.now(timezone.utc)
    if state:
        state.is_inside = is_inside
        state.last_transition_at = now
    else:
        state = GeofenceState(
            geofence_id=geofence_id,
            child_id=child_id,
            is_inside=is_inside,
            last_transition_at=now
        )
        session.add(state)
    await session.flush()
    return state


async def set_child_sos(session: AsyncSession, child_id: int, is_active: bool = True) -> Optional[User]:
    """Set child SOS status flag."""
    child = await get_user(session, child_id)
    if child:
        child.is_sos_active = is_active
        await session.flush()
    return child


set_sos_status = set_child_sos


async def check_and_update_battery_alert(
    session: AsyncSession,
    child_id: int,
    battery_level: int
) -> bool:
    """
    Returns True if an alert should be sent to the parent (level <= 20% and not already alerted recently).
    """
    if battery_level > 20:
        return False

    result = await session.execute(
        select(BatteryAlertLog).where(BatteryAlertLog.child_id == child_id)
    )
    log = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if not log:
        new_log = BatteryAlertLog(
            child_id=child_id,
            last_alerted_level=battery_level,
            last_alerted_at=now
        )
        session.add(new_log)
        await session.flush()
        return True

    # If alerted more than 2 hours ago or dropped by 5%
    last_alert = log.last_alerted_at
    if last_alert and last_alert.tzinfo is None:
        last_alert = last_alert.replace(tzinfo=timezone.utc)
    time_diff = (now - last_alert).total_seconds() if last_alert else 999999
    if time_diff > 7200 or (log.last_alerted_level - battery_level >= 5):
        log.last_alerted_level = battery_level
        log.last_alerted_at = now
        await session.flush()
        return True

    return False


async def create_task(
    session: AsyncSession,
    parent_id: int,
    child_id: int,
    title: str,
    description: Optional[str] = None,
    reward_coins: int = 1000,
    media_type: Optional[str] = None,
    media_url: Optional[str] = None
) -> Task:
    """Create a task with gamified reward and optional photo/video attachment."""
    task = Task(
        parent_id=parent_id,
        child_id=child_id,
        title=title.strip(),
        description=description.strip() if description else None,
        reward_coins=reward_coins,
        status="pending",
        media_type=media_type,
        media_url=media_url
    )
    session.add(task)
    await session.flush()
    return task


async def mark_task_seen(session: AsyncSession, task_id: int) -> Optional[Task]:
    """Mark task as viewed by child."""
    task = await get_task_by_id(session, task_id)
    if task:
        task.seen_at = datetime.now(timezone.utc)
        if task.status == "pending":
            task.status = "seen"
        await session.flush()
    return task


async def set_task_schedule(session: AsyncSession, task_id: int, promised_time: str) -> Optional[Task]:
    """Child commits to a specific time to complete the task."""
    task = await get_task_by_id(session, task_id)
    if task:
        task.promised_time = promised_time.strip()
        if task.status in ["pending", "seen"]:
            task.status = "scheduled"
        await session.flush()
    return task


async def get_tasks_for_child(
    session: AsyncSession,
    child_id: int,
    status: Optional[str] = None
) -> List[Task]:
    """Fetch tasks assigned to child."""
    query = select(Task).where(Task.child_id == child_id)
    if status:
        query = query.where(Task.status == status)
    query = query.order_by(Task.created_at.desc())
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_tasks_for_parent(
    session: AsyncSession,
    parent_id: int,
    status: Optional[str] = None,
    child_id: Optional[int] = None
) -> List[Task]:
    """Fetch tasks created by parent."""
    query = select(Task).where(Task.parent_id == parent_id)
    if child_id:
        query = query.where(Task.child_id == child_id)
    if status:
        query = query.where(Task.status == status)
    query = query.order_by(Task.created_at.desc())
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_task_by_id(session: AsyncSession, task_id: int) -> Optional[Task]:
    """Retrieve task by ID."""
    result = await session.execute(select(Task).where(Task.id == task_id))
    return result.scalar_one_or_none()


async def submit_task_completed(
    session: AsyncSession,
    task_id: int,
    proof_photo_url: Optional[str] = None
) -> Optional[Task]:
    """Child marks task as completed with photo proof, awaiting parent approval."""
    task = await get_task_by_id(session, task_id)
    if task:
        task.status = "completed_by_child"
        if proof_photo_url:
            task.proof_photo_url = proof_photo_url
        await session.flush()
    return task


async def approve_task(session: AsyncSession, task_id: int) -> Optional[Task]:
    """Parent approves task, child receives reward coins."""
    task = await get_task_by_id(session, task_id)
    if task and task.status in ["pending", "seen", "scheduled", "completed_by_child"]:
        task.status = "approved"
        task.completed_at = datetime.now(timezone.utc)
        # Credit child balance
        child = await get_user(session, task.child_id)
        if child:
            child.virtual_balance += task.reward_coins
        await session.flush()
    return task


async def reject_task(session: AsyncSession, task_id: int) -> Optional[Task]:
    """Parent rejects task."""
    task = await get_task_by_id(session, task_id)
    if task:
        task.status = "rejected"
        await session.flush()
    return task


async def delete_task(session: AsyncSession, task_id: int, parent_id: Optional[int] = None) -> bool:
    """Delete a task by ID, optionally verifying parent ownership."""
    task = await get_task_by_id(session, task_id)
    if not task:
        return False
    if parent_id and task.parent_id != parent_id:
        return False
    await session.delete(task)
    await session.flush()
    return True


async def reset_user_role(session: AsyncSession, user_id: int) -> Tuple[bool, List[int]]:
    """
    Reset user role back to 'unassigned', clearing pairing code and links.
    Returns (success, list_of_notified_user_ids).
    """
    user = await get_user(session, user_id)
    if not user:
        return False, []

    notified_ids: List[int] = []
    # If user was a child, find linked parents to notify
    if user.role == "child":
        parents = await get_parents_for_child(session, user_id)
        notified_ids.extend([p.id for p in parents])
        await session.execute(
            delete(ParentChildLink).where(ParentChildLink.child_id == user_id)
        )
    # If user was a parent, find linked children to notify
    elif user.role == "parent":
        children = await get_children_for_parent(session, user_id)
        notified_ids.extend([c.id for c in children])
        await session.execute(
            delete(ParentChildLink).where(ParentChildLink.parent_id == user_id)
        )

    user.role = "unassigned"
    user.pairing_code = None
    user.pairing_code_created_at = None
    user.web_access_code = None
    user.is_sos_active = False
    await session.flush()
    return True, notified_ids


# ---------------------------------------------------------
# Wishlist / Orzular Qutisi CRUD
# ---------------------------------------------------------

async def update_child_wishlist(
    session: AsyncSession,
    child_id: int,
    title: str,
    target_som: int
) -> Optional[User]:
    """Set or update child's dream wishlist and target savings amount."""
    child = await get_user(session, child_id)
    if child:
        child.wishlist_title = title.strip() if title else ""
        child.wishlist_target_som = max(1000, target_som)
        await session.flush()
    return child


async def get_child_wishlist(session: AsyncSession, child_id: int) -> Optional[dict]:
    """Retrieve child's savings target and calculate current progress percentage."""
    child = await get_user(session, child_id)
    if not child or not child.wishlist_title or not child.wishlist_target_som or child.wishlist_target_som <= 0:
        return None
    rate_coins, rate_som = await get_coin_rate_for_user(session, child_id)
    current_som = calculate_som_from_coins(child.virtual_balance or 0, rate_coins, rate_som)
    title = child.wishlist_title
    target_som = child.wishlist_target_som
    progress = min(100, int((current_som / target_som) * 100)) if target_som > 0 else 0
    return {
        "title": title,
        "target_som": target_som,
        "current_som": current_som,
        "progress_percent": progress,
        "is_achieved": current_som >= target_som
    }


# ---------------------------------------------------------
# "Xavfsiz Yo'l" (Walk Safe Timer Mode) CRUD
# ---------------------------------------------------------

async def start_walk_safe(
    session: AsyncSession,
    child_id: int,
    duration_minutes: int,
    destination: str = "Uyga qaytish"
) -> Optional[User]:
    """Start an active Walk Safe countdown journey for child."""
    child = await get_user(session, child_id)
    if child:
        now = datetime.now(timezone.utc)
        duration = max(5, min(180, duration_minutes))
        child.walk_safe_active = True
        child.walk_safe_duration = duration
        child.walk_safe_destination = destination.strip() or "Uyga qaytish"
        child.walk_safe_started_at = now
        child.walk_safe_deadline = now + timedelta(minutes=duration)
        child.walk_safe_last_checkin = now
        await session.flush()
    return child


async def checkin_walk_safe(session: AsyncSession, child_id: int) -> Optional[User]:
    """Child confirms 'I am safe' periodic check-in."""
    child = await get_user(session, child_id)
    if child and child.walk_safe_active:
        child.walk_safe_last_checkin = datetime.now(timezone.utc)
        await session.flush()
    return child


async def complete_walk_safe(session: AsyncSession, child_id: int) -> Optional[User]:
    """Child marks walk safe session completed upon safe arrival."""
    child = await get_user(session, child_id)
    if child:
        child.walk_safe_active = False
        child.walk_safe_deadline = None
        child.walk_safe_started_at = None
        await session.flush()
    return child


async def get_active_walk_safe(session: AsyncSession, child_id: int) -> Optional[dict]:
    """Retrieve current Walk Safe countdown status."""
    child = await get_user(session, child_id)
    if not child or not child.walk_safe_active or not child.walk_safe_deadline:
        return None
    now = datetime.now(timezone.utc)
    ws_deadline = child.walk_safe_deadline
    if ws_deadline and ws_deadline.tzinfo is None:
        ws_deadline = ws_deadline.replace(tzinfo=timezone.utc)
    remaining_seconds = int((ws_deadline - now).total_seconds()) if ws_deadline else 0
    is_expired = remaining_seconds <= 0
    return {
        "is_active": True,
        "duration_minutes": child.walk_safe_duration,
        "destination": child.walk_safe_destination or "Uyga qaytish",
        "started_at": child.walk_safe_started_at.isoformat() if child.walk_safe_started_at else None,
        "deadline": child.walk_safe_deadline.isoformat() if child.walk_safe_deadline else None,
        "remaining_seconds": max(0, remaining_seconds),
        "is_expired": is_expired,
        "last_checkin": child.walk_safe_last_checkin.isoformat() if child.walk_safe_last_checkin else None
    }


# ---------------------------------------------------------
# Daily Safety Report Infographic Generator
# ---------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in kilometers between two GPS points using Haversine formula."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


async def generate_daily_safety_report(session: AsyncSession, child_id: int) -> dict:
    """Compute comprehensive daily movement and safety statistics for evening report."""
    child = await get_user(session, child_id)
    child_name = child.full_name if child and child.full_name else f"Farzand #{child_id}"

    # 1. Location logs for today
    logs = await get_location_history_today(session, child_id, limit=300)

    total_km = 0.0
    max_speed = 0.0
    speeds = []
    last_anchor = None

    for l in logs:
        if last_anchor is None:
            last_anchor = l
            continue

        dist = haversine_km(last_anchor.latitude, last_anchor.longitude, l.latitude, l.longitude)
        if dist > 50.0:  # Glitch jump
            continue

        # GPS deadband filter: ignore stationary sensor drift (< 15 meters / 0.015 km)
        if dist >= 0.015:
            total_km += dist
            last_anchor = l

        if l.speed is not None and l.speed > 0:
            speeds.append(l.speed)
            if l.speed > max_speed:
                max_speed = l.speed

    tashkent_tz = timezone(timedelta(hours=5))
    first_signal = None
    last_signal = None
    if logs:
        if logs[0].recorded_at:
            dt0 = logs[0].recorded_at if logs[0].recorded_at.tzinfo else logs[0].recorded_at.replace(tzinfo=timezone.utc)
            first_signal = dt0.astimezone(tashkent_tz).strftime('%H:%M')
        if logs[-1].recorded_at:
            dt1 = logs[-1].recorded_at if logs[-1].recorded_at.tzinfo else logs[-1].recorded_at.replace(tzinfo=timezone.utc)
            last_signal = dt1.astimezone(tashkent_tz).strftime('%H:%M')
    battery_level = logs[-1].battery_level if logs and logs[-1].battery_level is not None else None

    # Format distance nicely (metres when < 1 km)
    if total_km < 1.0:
        meters = int(round(total_km * 1000))
        distance_formatted = f"{meters} metr ({total_km:.2f} km)" if meters > 0 else "0 metr"
    else:
        distance_formatted = f"{total_km:.2f} km"

    # 2. Geofences visited today
    geofences = await get_geofences_for_child(session, child_id)
    visited_zones = []
    for g in geofences:
        inside_count = 0
        for l in logs:
            if haversine_km(g.latitude, g.longitude, l.latitude, l.longitude) * 1000.0 <= g.radius_meters:
                inside_count += 1
        if inside_count > 0:
            count_str = "100+" if inside_count >= 100 else str(inside_count)
            visited_zones.append({
                "name": g.name,
                "points_inside": inside_count,
                "points_str": count_str,
                "est_minutes": min(720, inside_count * 2)
            })

    # 3. Tasks completed today
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    tasks_res = await session.execute(
        select(Task).where(
            Task.child_id == child_id,
            Task.status == "approved",
            Task.completed_at >= cutoff
        )
    )
    completed_tasks = list(tasks_res.scalars().all())
    rate_coins, rate_som = await get_coin_rate_for_user(session, child_id)
    earned_coins = sum(t.reward_coins for t in completed_tasks)
    earned_som = calculate_som_from_coins(earned_coins, rate_coins, rate_som)

    # 4. Wishlist progress (None if not configured)
    wishlist = await get_child_wishlist(session, child_id)

    return {
        "child_id": child_id,
        "child_name": child_name,
        "points_count": len(logs),
        "total_distance_km": round(total_km, 2),
        "distance_formatted": distance_formatted,
        "max_speed_kmh": round(max_speed, 1),
        "first_signal": first_signal,
        "last_signal": last_signal,
        "battery_level": battery_level,
        "visited_zones": visited_zones,
        "completed_tasks_count": len(completed_tasks),
        "earned_som": earned_som,
        "earned_coins": earned_coins,
        "wishlist": wishlist
    }


# ==============================================================================
# SUPER-ADMIN & OWNER BOT CRUD OPERATIONS
# ==============================================================================

async def get_admin_user(session: AsyncSession, user_id: int) -> Optional[AdminUser]:
    res = await session.execute(select(AdminUser).where(AdminUser.id == user_id))
    return res.scalar_one_or_none()


async def is_owner_or_admin(session: AsyncSession, user_id: int, username: Optional[str] = None) -> Tuple[bool, bool]:
    """Returns (is_admin, is_owner). Checks owner username 'fayzbek1331' and DB admin table."""
    from config import settings
    clean_uname = (username or "").lower().lstrip("@")
    owner_uname = (settings.OWNER_USERNAME or "fayzbek1331").lower().lstrip("@")
    
    if clean_uname and clean_uname == owner_uname:
        return True, True
    
    admin = await get_admin_user(session, user_id)
    if admin:
        return True, admin.is_owner
    return False, False


async def ensure_owner_registered(session: AsyncSession, user_id: int, username: Optional[str], full_name: Optional[str]) -> AdminUser:
    """Ensures @fayzbek1331 is registered with is_owner=True in DB."""
    from config import settings
    admin = await get_admin_user(session, user_id)
    clean_username = (username or settings.OWNER_USERNAME).lstrip("@")
    if not admin:
        admin = AdminUser(
            id=user_id,
            username=clean_username,
            full_name=full_name or "Fayzbek (Loyiha asoschisi)",
            is_owner=True
        )
        session.add(admin)
        await session.commit()
    else:
        if not admin.is_owner or admin.username != clean_username:
            admin.is_owner = True
            admin.username = clean_username
            await session.commit()
    return admin


async def add_admin_user(session: AsyncSession, user_id: int, username: Optional[str], full_name: Optional[str], added_by: int) -> AdminUser:
    admin = await get_admin_user(session, user_id)
    clean_uname = username.lstrip("@") if username else None
    if not admin:
        admin = AdminUser(
            id=user_id,
            username=clean_uname,
            full_name=full_name or f"Admin #{user_id}",
            is_owner=False,
            added_by=added_by
        )
        session.add(admin)
    else:
        if clean_uname:
            admin.username = clean_uname
    await session.commit()
    return admin


async def remove_admin_user(session: AsyncSession, user_id: int) -> bool:
    res = await session.execute(delete(AdminUser).where(AdminUser.id == user_id, AdminUser.is_owner == False))
    await session.commit()
    return res.rowcount > 0


async def list_admin_users(session: AsyncSession) -> List[AdminUser]:
    res = await session.execute(select(AdminUser).order_by(AdminUser.is_owner.desc(), AdminUser.created_at.asc()))
    return list(res.scalars().all())


async def get_system_overview_stats(session: AsyncSession) -> dict:
    from sqlalchemy import func
    total_users = (await session.execute(select(func.count(User.id)))).scalar_one() or 0
    total_parents = (await session.execute(select(func.count(User.id)).where(User.role == "parent"))).scalar_one() or 0
    total_children = (await session.execute(select(func.count(User.id)).where(User.role == "child"))).scalar_one() or 0
    active_sos = (await session.execute(select(func.count(User.id)).where(User.is_sos_active.is_(True)))).scalar_one() or 0
    total_geofences = (await session.execute(select(func.count(Geofence.id)))).scalar_one() or 0
    total_tasks = (await session.execute(select(func.count(Task.id)))).scalar_one() or 0
    total_locs = (await session.execute(select(func.count(LocationLog.id)))).scalar_one() or 0

    tashkent_now = datetime.now(timezone(timedelta(hours=5)))
    today_start_tashkent = tashkent_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_tashkent.astimezone(timezone.utc)
    today_users = (await session.execute(select(func.count(User.id)).where(User.created_at >= today_start_utc))).scalar_one() or 0

    return {
        "total_users": total_users,
        "total_parents": total_parents,
        "total_children": total_children,
        "today_users": today_users,
        "active_sos": active_sos,
        "total_geofences": total_geofences,
        "total_tasks": total_tasks,
        "total_location_logs": total_locs
    }


async def search_user_by_query(session: AsyncSession, query: str) -> Optional[User]:
    """Search user by numeric ID or username."""
    from sqlalchemy import func
    clean_q = query.strip()
    if clean_q.startswith("@"):
        clean_q = clean_q[1:]

    if clean_q.isdigit():
        user = await get_user(session, int(clean_q))
        if user:
            return user

    res = await session.execute(select(User).where(func.lower(User.username) == clean_q.lower()))
    user = res.scalar_one_or_none()
    if user:
        return user

    # Try searching by full_name pattern
    res_name = await session.execute(select(User).where(User.full_name.ilike(f"%{clean_q}%")).limit(1))
    return res_name.scalar_one_or_none()


async def get_user_full_dossier(session: AsyncSession, user_id: int) -> Optional[dict]:
    user = await get_user(session, user_id)
    if not user:
        return None

    latest_loc = await get_latest_location(session, user_id)
    parents = await get_parents_for_child(session, user_id)
    children = await get_children_for_parent(session, user_id)
    tasks = await get_tasks_for_child(session, user_id) if user.role == "child" else []
    geofences = await get_geofences_for_parent(session, user_id) if user.role == "parent" else []

    rate_coins, rate_som = await get_coin_rate_for_user(session, user_id)
    balance_som = calculate_som_from_coins(user.virtual_balance, rate_coins, rate_som)
    policy = await get_or_create_device_policy(session, user_id) if user.role == "child" else None

    return {
        "user": user,
        "latest_location": latest_loc,
        "parents": parents,
        "children": children,
        "tasks": tasks,
        "geofences": geofences,
        "balance_som": balance_som,
        "rate_coins": rate_coins,
        "rate_som": rate_som,
        "policy": policy
    }


async def get_active_sos_list(session: AsyncSession) -> List[User]:
    res = await session.execute(select(User).where(User.is_sos_active.is_(True)).order_by(User.updated_at.desc()))
    return list(res.scalars().all())


async def get_recent_registered_users(session: AsyncSession, limit: int = 10) -> List[User]:
    res = await session.execute(select(User).order_by(User.created_at.desc()).limit(limit))
    return list(res.scalars().all())


async def force_regenerate_user_code(session: AsyncSession, user_id: int) -> Optional[str]:
    """Force regenerates pairing code or web access code for a user."""
    user = await get_user(session, user_id)
    if not user:
        return None
    if user.role == "parent":
        for _ in range(30):
            chars = string.ascii_uppercase + string.digits
            new_code = "".join(random.choices(chars, k=6))
            existing = await session.execute(
                select(User).where((User.pairing_code == new_code) | (User.web_access_code == new_code))
            )
            if not existing.scalars().first():
                user.pairing_code = new_code
                user.pairing_code_created_at = datetime.now(timezone.utc)
                await session.commit()
                return new_code
    else:
        for _ in range(30):
            new_code = "".join(random.choices(string.digits, k=6))
            existing = await session.execute(
                select(User).where((User.web_access_code == new_code) | (User.pairing_code == new_code))
            )
            if not existing.scalars().first():
                user.web_access_code = new_code
                await session.commit()
                return new_code
    return None


async def unlink_all_for_user(session: AsyncSession, user_id: int) -> int:
    """Deletes all ParentChildLink associations for this user (both parent and child side)."""
    res = await session.execute(
        delete(ParentChildLink).where(
            or_(ParentChildLink.parent_id == user_id, ParentChildLink.child_id == user_id)
        )
    )
    await session.commit()
    return res.rowcount


async def add_coins_to_user(session: AsyncSession, user_id: int, amount: int) -> Optional[int]:
    """Adds (or removes if negative) virtual coins to a user."""
    user = await get_user(session, user_id)
    if not user:
        return None
    user.virtual_balance = max(0, (user.virtual_balance or 0) + amount)
    await session.commit()
    return user.virtual_balance


async def get_all_user_ids_by_role(session: AsyncSession, role: Optional[str] = None) -> List[int]:
    """Get all user Telegram IDs, optionally filtered by role ('parent', 'child', or None for all)."""
    stmt = select(User.id).where(User.is_active == True)
    if role in ("parent", "child"):
        stmt = stmt.where(User.role == role)
    res = await session.execute(stmt)
    return list(res.scalars().all())


# ==============================================================================
# DEVICE POLICY, APP BLOCKING & SCREEN TIME MONITORING
# ==============================================================================

def hash_parent_pin(pin: str) -> str:
    """Hash 4-digit parent PIN using SHA-256."""
    return hashlib.sha256(str(pin).strip().encode()).hexdigest()


DEFAULT_POPULAR_APPS = [
    {"package": "com.zhiliaoapp.musically", "app_name": "TikTok", "category": "social", "icon": "video", "default_blocked": False},
    {"package": "com.google.android.youtube", "app_name": "YouTube", "category": "video", "icon": "youtube", "default_blocked": False},
    {"package": "com.instagram.android", "app_name": "Instagram", "category": "social", "icon": "instagram", "default_blocked": False},
    {"package": "com.tencent.ig", "app_name": "PUBG Mobile", "category": "game", "icon": "gamepad-2", "default_blocked": False},
    {"package": "com.roblox.client", "app_name": "Roblox", "category": "game", "icon": "gamepad-2", "default_blocked": False},
    {"package": "com.supercell.brawlstars", "app_name": "Brawl Stars", "category": "game", "icon": "gamepad-2", "default_blocked": False},
    {"package": "com.dts.freefireth", "app_name": "Free Fire", "category": "game", "icon": "gamepad-2", "default_blocked": False},
    {"package": "com.supercell.clashofclans", "app_name": "Clash of Clans", "category": "game", "icon": "gamepad-2", "default_blocked": False},
    {"package": "com.mojang.minecraftpe", "app_name": "Minecraft", "category": "game", "icon": "gamepad-2", "default_blocked": False},
    {"package": "org.telegram.messenger", "app_name": "Telegram", "category": "chat", "icon": "message-circle", "default_blocked": False},
    {"package": "com.whatsapp", "app_name": "WhatsApp", "category": "chat", "icon": "message-circle", "default_blocked": False},
    {"package": "com.snapchat.android", "app_name": "Snapchat", "category": "social", "icon": "video", "default_blocked": False},
    {"package": "video.like", "app_name": "Likee", "category": "social", "icon": "video", "default_blocked": False},
    {"package": "com.android.chrome", "app_name": "Google Chrome", "category": "browser", "icon": "globe", "default_blocked": False},
]


async def get_or_create_device_policy(
    session: AsyncSession,
    child_id: int,
    parent_id: Optional[int] = None
) -> ChildDevicePolicy:
    """Fetch existing device policy for child or create default."""
    stmt = select(ChildDevicePolicy).where(ChildDevicePolicy.child_id == child_id)
    res = await session.execute(stmt)
    policy = res.scalar_one_or_none()
    if not policy:
        default_pin_hash = hash_parent_pin("1234")
        policy = ChildDevicePolicy(
            child_id=child_id,
            parent_id=parent_id,
            is_all_apps_blocked=False,
            lockdown_reason="Dars vaqti",
            blocked_packages="[]",
            parent_pin_hash=default_pin_hash,
            anti_uninstall_enabled=True
        )
        session.add(policy)
        await session.commit()
        await session.refresh(policy)
    elif parent_id and not policy.parent_id:
        policy.parent_id = parent_id
        await session.commit()
    return policy


async def update_device_policy(
    session: AsyncSession,
    child_id: int,
    parent_id: Optional[int] = None,
    is_all_apps_blocked: Optional[bool] = None,
    is_network_restricted: Optional[bool] = None,
    lockdown_reason: Optional[str] = None,
    blocked_packages: Optional[List[str]] = None,
    anti_uninstall_enabled: Optional[bool] = None,
    new_pin: Optional[str] = None,
    parent_pin: Optional[str] = None,
    bedtime_start: Optional[str] = None,
    bedtime_end: Optional[str] = None,
    daily_limit_minutes: Optional[int] = None
) -> ChildDevicePolicy:
    """Update child device policy (lock all apps, selective blocks, network restriction, bedtime, anti-uninstall shield, or PIN)."""
    policy = await get_or_create_device_policy(session, child_id, parent_id)
    if is_all_apps_blocked is not None:
        policy.is_all_apps_blocked = is_all_apps_blocked
    if is_network_restricted is not None:
        policy.is_network_restricted = is_network_restricted
    if lockdown_reason is not None:
        policy.lockdown_reason = lockdown_reason.strip()
    if bedtime_start is not None:
        policy.bedtime_start = bedtime_start.strip()
    if bedtime_end is not None:
        policy.bedtime_end = bedtime_end.strip()
    if daily_limit_minutes is not None:
        policy.daily_limit_minutes = daily_limit_minutes
    if blocked_packages is not None:
        if isinstance(blocked_packages, list):
            clean_list = []
            for b in blocked_packages:
                if b and isinstance(b, str) and b.strip() and b.strip() not in clean_list:
                    clean_list.append(b.strip())
            policy.blocked_packages = json.dumps(clean_list)
        else:
            policy.blocked_packages = str(blocked_packages)
    if anti_uninstall_enabled is not None:
        policy.anti_uninstall_enabled = anti_uninstall_enabled

    effective_pin = new_pin or parent_pin
    if effective_pin and len(str(effective_pin).strip()) >= 4:
        policy.parent_pin_hash = hash_parent_pin(str(effective_pin).strip())
    policy.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(policy)
    return policy


async def verify_parent_pin(session: AsyncSession, child_id: int, pin: str) -> bool:
    """Verify 4-digit parent PIN for sensitive device settings and app unlocking."""
    policy = await get_or_create_device_policy(session, child_id)
    if not policy.parent_pin_hash:
        return True
    return policy.parent_pin_hash == hash_parent_pin(pin)


async def set_parent_pin(session: AsyncSession, child_id: int, pin: str) -> bool:
    """Set new parent PIN code (minimum 4 digits)."""
    if len(str(pin).strip()) < 4:
        return False
    policy = await get_or_create_device_policy(session, child_id)
    policy.parent_pin_hash = hash_parent_pin(pin)
    policy.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return True


async def save_app_usage_report(
    session: AsyncSession,
    child_id: int,
    total_screen_time_seconds: int,
    unlock_count: int,
    app_stats: Optional[list] = None,
    apps: Optional[list] = None,
    report_date: Optional[str] = None
) -> AppUsageReport:
    """Save daily app usage telemetry from child's device."""
    stats = app_stats if app_stats is not None else (apps or [])
    tashkent_tz = timezone(timedelta(hours=5))
    if not report_date:
        report_date = datetime.now(tashkent_tz).strftime("%Y-%m-%d")
    stmt = select(AppUsageReport).where(
        AppUsageReport.child_id == child_id,
        AppUsageReport.report_date == report_date
    )
    res = await session.execute(stmt)
    report = res.scalar_one_or_none()
    if not report:
        report = AppUsageReport(
            child_id=child_id,
            report_date=report_date,
            total_screen_time_seconds=total_screen_time_seconds,
            unlock_count=unlock_count,
            app_stats_json=json.dumps(stats)
        )
        session.add(report)
    else:
        report.total_screen_time_seconds = total_screen_time_seconds
        report.unlock_count = unlock_count
        report.app_stats_json = json.dumps(stats)
        report.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(report)
    return report

upsert_app_usage_report = save_app_usage_report


async def get_app_usage_report_today(session: AsyncSession, child_id: int) -> dict:
    """Fetch today's screen time report and per-app usage stats."""
    tashkent_tz = timezone(timedelta(hours=5))
    today_tashkent = datetime.now(tashkent_tz).strftime("%Y-%m-%d")
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stmt = select(AppUsageReport).where(
        AppUsageReport.child_id == child_id,
        AppUsageReport.report_date.in_([today_tashkent, today_utc])
    ).order_by(AppUsageReport.id.desc())
    res = await session.execute(stmt)
    report = res.scalars().first()

    # Fallback to the latest available report if today has none yet
    if not report:
        stmt_fb = select(AppUsageReport).where(
            AppUsageReport.child_id == child_id
        ).order_by(AppUsageReport.id.desc()).limit(1)
        res_fb = await session.execute(stmt_fb)
        report = res_fb.scalars().first()

    policy = await get_or_create_device_policy(session, child_id)
    try:
        blocked_list = json.loads(policy.blocked_packages or "[]")
    except Exception:
        blocked_list = []

    if report and report.app_stats_json:
        try:
            apps = json.loads(report.app_stats_json)
        except Exception:
            apps = []
        total_seconds = report.total_screen_time_seconds
        unlock_count = report.unlock_count
        if total_seconds == 0 and apps:
            total_seconds = sum(int(a.get("usage_seconds") or 0) for a in apps)
    else:
        total_seconds = 0
        unlock_count = 0
        apps = []

    for a in apps:
        pkg = a.get("package")
        a["is_blocked"] = bool(policy.is_all_apps_blocked or (pkg in blocked_list))

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    formatted_time = f"{hours} soat {minutes} daqiqa" if hours > 0 else f"{minutes} daqiqa"

    # Category breakdown
    games_sec = sum(int(a.get("usage_seconds") or 0) for a in apps if a.get("category") == "game")
    social_sec = sum(int(a.get("usage_seconds") or 0) for a in apps if a.get("category") in ("social", "video"))
    chat_sec = sum(int(a.get("usage_seconds") or 0) for a in apps if a.get("category") == "chat")
    other_sec = max(0, total_seconds - (games_sec + social_sec + chat_sec))

    return {
        "report_date": report.report_date if report else today_tashkent,
        "total_screen_time_seconds": total_seconds,
        "total_screen_time_formatted": formatted_time,
        "unlock_count": unlock_count,
        "is_all_apps_blocked": policy.is_all_apps_blocked,
        "lockdown_reason": policy.lockdown_reason,
        "anti_uninstall_enabled": policy.anti_uninstall_enabled,
        "has_parent_pin": bool(policy.parent_pin_hash),
        "blocked_packages": blocked_list,
        "apps": apps,
        "popular_apps": DEFAULT_POPULAR_APPS,
        "breakdown": {
            "games_seconds": games_sec,
            "social_seconds": social_sec,
            "chat_seconds": chat_sec,
            "other_seconds": other_sec
        }
    }

