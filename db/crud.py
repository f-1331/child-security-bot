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
            await session.flush()
            return code

    code = str(int(datetime.now().timestamp()))[-6:]
    user.web_access_code = code
    await session.flush()
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
    """Link parent and child together, ensuring child role and nickname."""
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
        child.wishlist_title = title.strip() if title else "Yangi Velosiped 🚲"
        child.wishlist_target_som = max(1000, target_som)
        await session.flush()
    return child


async def get_child_wishlist(session: AsyncSession, child_id: int) -> dict:
    """Retrieve child's savings target and calculate current progress percentage."""
    child = await get_user(session, child_id)
    if not child:
        return {
            "title": "Yangi Velosiped 🚲",
            "target_som": 600000,
            "current_som": 0,
            "progress_percent": 0,
            "is_achieved": False
        }
    rate_coins, rate_som = await get_user_coin_rate(session, child_id)
    current_som = calculate_som_from_coins(child.virtual_balance or 0, rate_coins, rate_som)
    title = child.wishlist_title or "Yangi Velosiped 🚲"
    target_som = child.wishlist_target_som if child.wishlist_target_som and child.wishlist_target_som > 0 else 600000
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
    for i in range(len(logs) - 1):
        p1, p2 = logs[i], logs[i + 1]
        dist = haversine_km(p1.latitude, p1.longitude, p2.latitude, p2.longitude)
        if dist < 50.0:  # filter GPS glitch jumps
            total_km += dist
        if p2.speed is not None and p2.speed > 0:
            speeds.append(p2.speed)
            if p2.speed > max_speed:
                max_speed = p2.speed

    first_signal = logs[0].recorded_at.strftime('%H:%M') if logs else None
    last_signal = logs[-1].recorded_at.strftime('%H:%M') if logs else None
    battery_level = logs[-1].battery_level if logs and logs[-1].battery_level is not None else None

    # 2. Geofences visited today
    geofences = await get_geofences_for_child(session, child_id)
    visited_zones = []
    for g in geofences:
        inside_count = 0
        for l in logs:
            if haversine_km(g.latitude, g.longitude, l.latitude, l.longitude) * 1000.0 <= g.radius_meters:
                inside_count += 1
        if inside_count > 0:
            visited_zones.append({
                "name": g.name,
                "points_inside": inside_count,
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
    rate_coins, rate_som = await get_user_coin_rate(session, child_id)
    earned_coins = sum(t.reward_coins for t in completed_tasks)
    earned_som = calculate_som_from_coins(earned_coins, rate_coins, rate_som)

    # 4. Wishlist progress
    wishlist = await get_child_wishlist(session, child_id)

    return {
        "child_id": child_id,
        "child_name": child_name,
        "points_count": len(logs),
        "total_distance_km": round(total_km, 2),
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
