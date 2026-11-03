from __future__ import annotations

from fastapi import HTTPException

VALID_DOCUMENT_ROLES = {"employee", "manager", "hr", "admin"}
UPLOAD_ROLES = {"admin", "hr", "manager"}
MANAGER_SHARE_ROLES = {"employee", "manager"}


def normalize_shared_roles(uploader_role: str, requested_roles: list[str] | None) -> list[str] | None:
    role = uploader_role.strip().lower()
    requested = {str(item).strip().lower() for item in (requested_roles or []) if str(item).strip()}
    if not requested.issubset(VALID_DOCUMENT_ROLES):
        raise HTTPException(status_code=403, detail="You cannot grant access to an unknown role")
    if role == "manager":
        if not requested.issubset(MANAGER_SHARE_ROLES):
            raise HTTPException(status_code=403, detail="Managers may share with employees and managers only")
        return sorted(requested | {"manager", "hr", "admin"})
    if role == "hr":
        if not requested.issubset(VALID_DOCUMENT_ROLES):
            raise HTTPException(status_code=403, detail="HR cannot grant access to that role")
        return sorted(requested) if requested else None
    if role == "admin":
        return sorted(requested) if requested else None
    raise HTTPException(status_code=403, detail="You do not have permission to share documents")


def ensure_document_manager(user_id: str, role: str, document: dict) -> None:
    normalized_role = role.strip().lower()
    if normalized_role in {"admin", "hr"}:
        return
    if normalized_role != "manager" or document.get("uploaded_by") != user_id:
        raise HTTPException(status_code=403, detail="You can manage only documents you uploaded")


def validate_pdf_upload(filename: str | None, content: bytes, max_bytes: int = 20 * 1024 * 1024) -> None:
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="PDF files must be 20 MB or smaller")
    if not filename or not filename.lower().endswith(".pdf") or not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="Upload a valid PDF file")
