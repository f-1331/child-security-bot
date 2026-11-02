import logging
from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User, ParentChildLink, ChildDevicePolicy, LocationLog, Geofence
from db.crud import hash_parent_pin

logger = logging.getLogger("smart_parental_tracker.seeds")


async def ensure_core_seeds(session: AsyncSession) -> dict:
    """
    Guarantees that core production parent, child, links, policies, and initial location
    exist and are up to date on ANY environment (Render PostgreSQL, Neon, SQLite).
    Safe to execute multiple times (idempotent upsert).
    """
    report = {
        "users_updated_or_created": [],
        "links_verified": [],
        "policies_verified": [],
        "locations_verified": []
    }

    # 1. OWNER / PARENT (Fayzbek, Telegram ID 6707839718)
    p1 = await session.get(User, 6707839718)
    if not p1:
        p1 = User(
            id=6707839718,
            role="parent",
            full_name="Fayzbek (Asoschi)",
            username="fayzbek1331",
            phone_number="+998772723101",
            web_access_code="67078397",
            pairing_code="67078397",
            virtual_balance=0,
            coin_rate_coins=1000,
            coin_rate_som=10000,
            is_active=True,
            is_sos_active=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        session.add(p1)
        report["users_updated_or_created"].append("created:6707839718")
    else:
        p1.role = "parent"
        p1.web_access_code = "67078397"
        p1.pairing_code = "67078397"
        p1.full_name = p1.full_name or "Fayzbek (Asoschi)"
        p1.username = p1.username or "fayzbek1331"
        p1.phone_number = p1.phone_number or "+998772723101"
        p1.is_active = True
        report["users_updated_or_created"].append("updated:6707839718")

    # 2. OWNER ALIAS USER ID 1
    p_alias = await session.get(User, 1)
    if not p_alias:
        p_alias = User(
            id=1,
            role="parent",
            full_name="Fayzbek (Asoschi)",
            username="fayzbek1331",
            phone_number="+998772723101",
            web_access_code="31238560",
            pairing_code="31238560",
            virtual_balance=0,
            coin_rate_coins=1000,
            coin_rate_som=10000,
            is_active=True,
            is_sos_active=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        session.add(p_alias)
        report["users_updated_or_created"].append("created:1")
    else:
        p_alias.role = "parent"
        p_alias.web_access_code = p_alias.web_access_code or "31238560"
        p_alias.pairing_code = p_alias.pairing_code or "31238560"
        p_alias.is_active = True
        report["users_updated_or_created"].append("updated:1")

    # 3. REAL CHILD (F.FUZAYL., Telegram ID 997185613)
    c1 = await session.get(User, 997185613)
    if not c1:
        c1 = User(
            id=997185613,
            role="child",
            full_name="F.FUZAYL.",
            username="FiruzbekFaybek",
            web_access_code="29909881",
            virtual_balance=500,
            is_active=True,
            is_sos_active=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        session.add(c1)
        report["users_updated_or_created"].append("created:997185613")
    else:
        c1.role = "child"
        c1.web_access_code = "29909881"
        c1.full_name = c1.full_name or "F.FUZAYL."
        c1.username = c1.username or "FiruzbekFaybek"
        c1.is_active = True
        report["users_updated_or_created"].append("updated:997185613")

    # 4. DEMO USERS (1001 & 2002)
    demo_p = await session.get(User, 1001)
    if not demo_p:
        demo_p = User(
            id=1001,
            role="parent",
            full_name="Rustam Karimov (Ota)",
            username="rustam_parent",
            web_access_code="12707685",
            pairing_code="12707685",
            virtual_balance=0,
            is_active=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        session.add(demo_p)
        report["users_updated_or_created"].append("created:1001")
    else:
        demo_p.web_access_code = demo_p.web_access_code or "12707685"
        demo_p.pairing_code = demo_p.pairing_code or "12707685"

    demo_c = await session.get(User, 2002)
    if not demo_c:
        demo_c = User(
            id=2002,
            role="child",
            full_name="Azizbek Karimov (Farzand)",
            username="azizbek_child",
            web_access_code="21092869",
            virtual_balance=250,
            is_active=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        session.add(demo_c)
        report["users_updated_or_created"].append("created:2002")
    else:
        demo_c.web_access_code = demo_c.web_access_code or "21092869"

    await session.flush()

    # 5. PARENT-CHILD LINKS
    # Link: 6707839718 <-> 997185613
    l1 = (await session.execute(
        select(ParentChildLink).where(
            ParentChildLink.parent_id == 6707839718,
            ParentChildLink.child_id == 997185613
        )
    )).scalar_one_or_none()
    if not l1:
        session.add(ParentChildLink(parent_id=6707839718, child_id=997185613, nickname="Fuzayl"))
        report["links_verified"].append("created:6707839718->997185613")
    else:
        report["links_verified"].append("exists:6707839718->997185613")

    # Link: 1 <-> 997185613
    l2 = (await session.execute(
        select(ParentChildLink).where(
            ParentChildLink.parent_id == 1,
            ParentChildLink.child_id == 997185613
        )
    )).scalar_one_or_none()
    if not l2:
        session.add(ParentChildLink(parent_id=1, child_id=997185613, nickname="Fuzayl"))
        report["links_verified"].append("created:1->997185613")
    else:
        report["links_verified"].append("exists:1->997185613")

    # Link: 1001 <-> 2002
    l_demo = (await session.execute(
        select(ParentChildLink).where(
            ParentChildLink.parent_id == 1001,
            ParentChildLink.child_id == 2002
        )
    )).scalar_one_or_none()
    if not l_demo:
        session.add(ParentChildLink(parent_id=1001, child_id=2002, nickname="Azizbek"))
        report["links_verified"].append("created:1001->2002")
    else:
        report["links_verified"].append("exists:1001->2002")

    await session.flush()

    # 6. CHILD POLICIES
    pol1 = (await session.execute(
        select(ChildDevicePolicy).where(ChildDevicePolicy.child_id == 997185613)
    )).scalar_one_or_none()
    if not pol1:
        session.add(ChildDevicePolicy(
            child_id=997185613,
            parent_id=6707839718,
            is_all_apps_blocked=False,
            is_network_restricted=False,
            lockdown_reason="Dars vaqti",
            blocked_packages="[]",
            anti_uninstall_enabled=True,
            daily_walking_goal_km=4.0,
            daily_walking_goal_steps=5700,
            parent_pin_hash=hash_parent_pin("1234")
        ))
        report["policies_verified"].append("created:997185613")
    else:
        if getattr(pol1, "daily_walking_goal_km", None) is None:
            pol1.daily_walking_goal_km = 4.0
        if getattr(pol1, "daily_walking_goal_steps", None) is None:
            pol1.daily_walking_goal_steps = 5700
        report["policies_verified"].append("exists:997185613")

    pol_demo = (await session.execute(
        select(ChildDevicePolicy).where(ChildDevicePolicy.child_id == 2002)
    )).scalar_one_or_none()
    if not pol_demo:
        session.add(ChildDevicePolicy(
            child_id=2002,
            parent_id=1001,
            is_all_apps_blocked=False,
            is_network_restricted=False,
            lockdown_reason="Dars vaqti",
            blocked_packages="[]",
            anti_uninstall_enabled=True,
            daily_walking_goal_km=3.0,
            daily_walking_goal_steps=4300,
            parent_pin_hash=hash_parent_pin("1234")
        ))
        report["policies_verified"].append("created:2002")
    else:
        report["policies_verified"].append("exists:2002")

    # 7. INITIAL LOCATION FOR CHILD 997185613
    loc = (await session.execute(
        select(LocationLog).where(LocationLog.child_id == 997185613).order_by(LocationLog.id.desc())
    )).scalars().first()
    if not loc:
        session.add(LocationLog(
            child_id=997185613,
            latitude=41.311081,
            longitude=69.240562,
            accuracy=10.0,
            speed=0.0,
            battery_level=85,
            is_charging=False,
            sound_mode="normal",
            source="telegram_live",
            recorded_at=datetime.utcnow()
        ))
        report["locations_verified"].append("created:997185613")
    else:
        report["locations_verified"].append("exists:997185613")

    # 8. INITIAL GEOFENCES FOR CHILD 997185613
    gf_count = (await session.execute(
        select(func.count(Geofence.id)).where(Geofence.child_id == 997185613)
    )).scalar() or 0
    if gf_count == 0:
        session.add(Geofence(
            parent_id=6707839718,
            child_id=997185613,
            name="Uy (Xavfsiz)",
            latitude=41.311081,
            longitude=69.240562,
            radius_meters=250.0,
            is_active=True,
            created_at=datetime.utcnow()
        ))
        session.add(Geofence(
            parent_id=6707839718,
            child_id=997185613,
            name="Maktab (Xavfsiz)",
            latitude=41.316500,
            longitude=69.248200,
            radius_meters=300.0,
            is_active=True,
            created_at=datetime.utcnow()
        ))

    await session.commit()
    logger.info(f"Core seeds verification completed: {report}")
    return report
