import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from backend.ingestion.ingestion import supabase

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)

NETWORK_ERRORS = (httpx.TransportError, ConnectionError, OSError)


def _with_retry(call, attempts: int = 3, delay: float = 0.4):
    """Run call(), retrying on transient network errors such as an idle
    connection being closed by the remote host."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except NETWORK_ERRORS as exc:
            if attempt == attempts:
                raise
            logger.warning("Transient network error (%s); retry %d of %d", exc, attempt, attempts - 1)
            time.sleep(delay)


@dataclass
class CurrentUser:
    user_id: str
    tenant_id: str
    role: str
    email: Optional[str] = None


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> CurrentUser:
    if creds is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        auth_res = _with_retry(lambda: supabase.auth.get_user(creds.credentials))
        user = auth_res.user if auth_res else None
    except NETWORK_ERRORS:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable. Please try again.")
    except Exception:
        user = None
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    try:
        rows = _with_retry(
            lambda: supabase.table("profiles")
            .select("tenant_id, role")
            .eq("id", user.id)
            .limit(1)
            .execute()
            .data
        )
    except NETWORK_ERRORS:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable. Please try again.")
    if not rows or not rows[0].get("tenant_id"):
        raise HTTPException(status_code=403, detail="No profile or tenant assigned to this user")

    normalized_role = str(rows[0].get("role") or "").strip().lower()
    if not normalized_role:
        raise HTTPException(status_code=403, detail="No role assigned to this user")
    return CurrentUser(
        user_id=user.id,
        tenant_id=rows[0]["tenant_id"],
        role=normalized_role,
        email=user.email,
    )


def require_roles(*allowed: str):
    allowed_lower = {role.lower() for role in allowed}

    def checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role.lower() not in allowed_lower:
            raise HTTPException(status_code=403, detail="You do not have permission to do this")
        return user
    return checker