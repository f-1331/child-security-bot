import random
import string
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple
from sqlalchemy import desc, select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import (
    User,
    ParentChildLink,
    LocationLog,
    Geofence,
    GeofenceState,
    Task,
    BatteryAlertLog,
)


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
    await session.flush()
    return user, True


async def update_user_role(session: AsyncSession, user_id: int, role: str) -> Optional[User]:
    """Update role for user."""
    user = await get_user(session, user_id)
    if user:
        user.role = role
        await session.flush()
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
        await session.flush()
        return user.pairing_code

    if user.web_access_code:
        user.pairing_code = user.web_access_code
        user.pairing_code_created_at = datetime.now(timezone.utc)
        await session.flush()
        return user.web_access_code

    # Generate permanent 6-digit numeric code
    code = "".join(random.choices(string.digits, k=6))
    user.pairing_code = code
    user.web_access_code = code
    user.pairing_code_created_at = datetime.now(timezone.utc)
    await session.flush()
    return code


async def get_parent_by_pairing_code(session: AsyncSession, code: str) -> Optional[User]:
    """Find parent by their pairing code."""
    result = await session.execute(
        select(User).where(
            User.pairing_code == code.strip(),
            User.role == "parent"
        )
    )
    return result.scalar_one_or_none()


async def generate_web_access_code(session: AsyncSession, user_id: int) -> str:
    """Generate or retrieve a permanent 6-digit access code for website login.
    Parents and all their linked children share the same unified family code.
    """
    user = await get_user(session, user_id)
    if not user:
        user, _ = await get_or_create_user(session, user_id)

    if user.role == "parent":
        return await generate_pairing_code(session, user_id)

    if user.role == "child":
        parents = await get_parents_for_child(session, user_id)
        if parents and parents[0].pairing_code:
            user.web_access_code = parents[0].pairing_code
            await session.flush()
            return parents[0].pairing_code

    if user.web_access_code:
        return user.web_access_code

    # Generate unique 6-digit code
    for _ in range(10):
        code = "".join(random.choices(string.digits, k=6))
        existing = await get_user_by_web_access_code(session, code)
        if not existing:
            user.web_access_code = code
            await session.flush()
            return code

    code = str(int(datetime.now().timestamp()))[-6:]
    user.web_access_code = code
    await session.flush()
    return code


async def get_user_by_web_access_code(session: AsyncSession, code: str) -> Optional[User]:
    """Find user by web access code (or pairing code, or telegram ID fallback).
    If code belongs to a child who is linked to a parent, resolve to the parent
    so all linked children can be seen and managed together under one account.
    """
    code_clean = code.strip().replace(" ", "").replace("-", "")

    # 1. First prioritize parent user matching this code
    result = await session.execute(
        select(User).where(
            (User.role == "parent") &
            ((User.web_access_code == code_clean) | (User.pairing_code == code_clean))
        )
    )
    parent = result.scalars().first()
    if parent:
        return parent

    # 2. Check any user matching this code
    result = await session.execute(
        select(User).where(
            (User.web_access_code == code_clean) | (User.pairing_code == code_clean)
        )
    )
    user = result.scalars().first()
    if user:
        if user.role == "child":
            parents = await get_parents_for_child(session, user.id)
            if parents:
                return parents[0]
        return user

    # 3. Direct Telegram ID fallback (used by Telegram WebApp auto-login)
    if code_clean.isdigit():
        try:
            uid = int(code_clean)
            user_by_id = await get_user(session, uid)
            if user_by_id:
                return user_by_id
        except Exception:
            pass

    return None


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
) -> Tuple[ParentChildLink, bool]:
    """Link parent and child together."""
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
    if link:
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
    source: str = "telegram_live"
) -> LocationLog:
    """Record a location telemetry entry."""
    entry = LocationLog(
        child_id=child_id,
        latitude=latitude,
        longitude=longitude,
        accuracy=accuracy,
        battery_level=battery_level,
        is_charging=is_charging,
        sound_mode=sound_mode,
        source=source
    )
    session.add(entry)
    await session.flush()
    return entry


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
    limit: int = 150
) -> List[LocationLog]:
    """Fetch location history for today (last 24 hours)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await session.execute(
        select(LocationLog)
        .where(
            LocationLog.child_id == child_id,
            LocationLog.recorded_at >= cutoff
        )
        .order_by(LocationLog.recorded_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


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
        .where(Geofence.parent_id == parent_id, Geofence.is_active == True)
        .order_by(Geofence.created_at.desc())
    )
    return list(result.scalars().all())


async def get_active_geofences_for_child(session: AsyncSession, child_id: int) -> List[Geofence]:
    """Get all safe zones applicable to a child (from their parents)."""
    parents = await get_parents_for_child(session, child_id)
    if not parents:
        return []
    parent_ids = [p.id for p in parents]

    result = await session.execute(
        select(Geofence).where(
            Geofence.parent_id.in_(parent_ids),
            Geofence.is_active == True,
            (Geofence.child_id == None) | (Geofence.child_id == child_id)
        )
    )
    return list(result.scalars().all())


async def delete_geofence(session: AsyncSession, geofence_id: int, parent_id: int) -> bool:
    """Delete a geofence by ID if owned by parent."""
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
    time_diff = (now - log.last_alerted_at).total_seconds()
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
    status: Optional[str] = None
) -> List[Task]:
    """Fetch tasks created by parent."""
    query = select(Task).where(Task.parent_id == parent_id)
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
    await session.flush()
    return True, notified_ids
