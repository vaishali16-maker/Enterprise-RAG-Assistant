from dataclasses import dataclass
from typing import Optional
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from backend.ingestion.ingestion import supabase
bearer = HTTPBearer(auto_error=False)

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
        auth_res = supabase.auth.get_user(creds.credentials)
        user = auth_res.user if auth_res else None
    except Exception:
        user = None
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    rows = (
        supabase.table("profiles")
        .select("tenant_id, role")
        .eq("id", user.id)
        .limit(1)
        .execute()
        .data
    )
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
