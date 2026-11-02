import uuid
import time
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("smart_parental_tracker.login_approval")

# In-memory store: request_id -> dict
_approval_requests: Dict[str, Dict[str, Any]] = {}

APPROVAL_TIMEOUT_SECONDS = 120  # 2 minutes timeout


def create_approval_request(user_id: int, code: str, client_ip: str, user_agent: str = "") -> str:
    """Register a new pending login confirmation request."""
    now = time.time()
    # Cleanup expired requests
    expired_keys = [k for k, v in _approval_requests.items() if now > v.get("expires_at", 0)]
    for k in expired_keys:
        _approval_requests.pop(k, None)

    req_id = uuid.uuid4().hex[:12]
    _approval_requests[req_id] = {
        "request_id": req_id,
        "user_id": user_id,
        "code": code,
        "status": "pending",
        "client_ip": client_ip,
        "user_agent": user_agent,
        "created_at": now,
        "expires_at": now + APPROVAL_TIMEOUT_SECONDS,
        "telegram_msg_id": None
    }
    logger.info(f"Created 2FA login approval request {req_id} for user {user_id} (IP: {client_ip})")
    return req_id


def get_approval_request(req_id: str) -> Optional[Dict[str, Any]]:
    """Fetch status of pending request, checking expiration."""
    req = _approval_requests.get(req_id)
    if not req:
        return None
    if req.get("status") == "pending" and time.time() > req.get("expires_at", 0):
        req["status"] = "expired"
    return req


def set_approval_status(req_id: str, new_status: str) -> bool:
    """Update approval status ('approved' or 'rejected')."""
    req = _approval_requests.get(req_id)
    if not req:
        return False
    req["status"] = new_status
    logger.info(f"Updated 2FA login approval request {req_id} status to '{new_status}'")
    return True
