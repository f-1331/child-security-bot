import uuid
import time
import logging
from typing import Dict, Any, Optional
from sqlalchemy import select
from db.database import AsyncSessionLocal

logger = logging.getLogger("smart_parental_tracker.login_approval")

# In-memory store: request_id -> dict for fast retrieval
_approval_requests: Dict[str, Dict[str, Any]] = {}

APPROVAL_TIMEOUT_SECONDS = 600  # 10 minutes timeout window


async def create_approval_request(user_id: int, code: str, client_ip: str, user_agent: str = "") -> str:
    """Register a new pending login confirmation request in memory and persistent DB."""
    now = time.time()
    expires_at = now + APPROVAL_TIMEOUT_SECONDS

    # Memory cleanup for stale items
    expired_keys = [k for k, v in _approval_requests.items() if now > v.get("expires_at", 0)]
    for k in expired_keys:
        _approval_requests.pop(k, None)

    req_id = uuid.uuid4().hex[:12]
    payload = {
        "request_id": req_id,
        "user_id": user_id,
        "code": code,
        "status": "pending",
        "client_ip": client_ip,
        "user_agent": user_agent,
        "created_at": now,
        "expires_at": expires_at,
        "telegram_msg_id": None
    }
    _approval_requests[req_id] = payload

    # Persist directly into PostgreSQL
    try:
        from db.models import LoginApproval
        async with AsyncSessionLocal() as session:
            obj = LoginApproval(
                request_id=req_id,
                user_id=user_id,
                code=code,
                status="pending",
                client_ip=client_ip,
                user_agent=user_agent,
                created_at=now,
                expires_at=expires_at
            )
            session.add(obj)
            await session.commit()
            logger.info(f"Persisted 2FA login approval {req_id} into DB for user {user_id}")
    except Exception as e:
        logger.warning(f"Could not persist 2FA request to DB (fallback to RAM): {e}")

    return req_id


async def get_approval_request(req_id: str) -> Optional[Dict[str, Any]]:
    """Fetch status of pending request from memory or persistent DB."""
    req = _approval_requests.get(req_id)
    if not req:
        # Query persistent DB in case server redeployed/restarted
        try:
            from db.models import LoginApproval
            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    select(LoginApproval).where(LoginApproval.request_id == req_id)
                )
                db_obj = res.scalars().first()
                if db_obj:
                    req = {
                        "request_id": db_obj.request_id,
                        "user_id": db_obj.user_id,
                        "code": db_obj.code,
                        "status": db_obj.status,
                        "client_ip": db_obj.client_ip or "",
                        "user_agent": db_obj.user_agent or "",
                        "created_at": db_obj.created_at,
                        "expires_at": db_obj.expires_at,
                        "telegram_msg_id": None
                    }
                    _approval_requests[req_id] = req
        except Exception as e:
            logger.warning(f"Failed to query LoginApproval from DB for {req_id}: {e}")

    if not req:
        return None

    if req.get("status") == "pending" and time.time() > req.get("expires_at", 0):
        req["status"] = "expired"

    return req


def get_approval_request_sync(req_id: str) -> Optional[Dict[str, Any]]:
    """Synchronous memory-only lookup."""
    return _approval_requests.get(req_id)


async def set_approval_status(
    req_id: str,
    new_status: str,
    user_id: int = 6707839718,
    code: str = "1"
) -> bool:
    """Update approval status ('approved' or 'rejected') in memory and DB.
    Guarantees that if the Owner clicked 'allow', the session is approved even after container restart!
    """
    now = time.time()
    req = _approval_requests.get(req_id)
    if req:
        req["status"] = new_status
    else:
        req = {
            "request_id": req_id,
            "user_id": user_id,
            "code": code,
            "status": new_status,
            "client_ip": "Telegram-Approval",
            "user_agent": "Telegram Bot Callback",
            "created_at": now,
            "expires_at": now + APPROVAL_TIMEOUT_SECONDS,
            "telegram_msg_id": None
        }
        _approval_requests[req_id] = req

    # Persist update to DB
    try:
        from db.models import LoginApproval
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(LoginApproval).where(LoginApproval.request_id == req_id)
            )
            db_obj = res.scalars().first()
            if db_obj:
                db_obj.status = new_status
            else:
                db_obj = LoginApproval(
                    request_id=req_id,
                    user_id=user_id,
                    code=code,
                    status=new_status,
                    client_ip="Telegram-Approval",
                    user_agent="Telegram Bot Callback",
                    created_at=now,
                    expires_at=now + APPROVAL_TIMEOUT_SECONDS
                )
                session.add(db_obj)
            await session.commit()
            logger.info(f"Persisted 2FA status '{new_status}' in DB for request {req_id}")
    except Exception as e:
        logger.warning(f"Failed to persist approval status '{new_status}' to DB for {req_id}: {e}")

    return True
