from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.auth import current_user
from backend.app.core.errors import ApiError
from backend.app.core.security import require_local_api_access
from backend.app.db.session import get_db
from backend.app.models.auth import CaptureSession, User, UserRole, Zone, utcnow

router = APIRouter(prefix="/capture-sessions", tags=["capture-sessions"], dependencies=[Depends(require_local_api_access)])
SESSION_LIFETIME = timedelta(hours=8)


class StartBody(BaseModel):
    zone_id: str | None = Field(default=None, min_length=36, max_length=36)


def public_session(session: CaptureSession, zone: Zone) -> dict:
    return {"id": session.id, "zone": {"id": zone.id, "name": zone.name}, "status": session.status, "started_at": session.started_at.isoformat() + "Z", "expires_at": session.expires_at.isoformat() + "Z", "stopped_at": session.stopped_at.isoformat() + "Z" if session.stopped_at else None}


def active_for_user(db: Session, user: User) -> CaptureSession | None:
    session = db.scalar(select(CaptureSession).where(CaptureSession.user_id == user.id, CaptureSession.status == "active").with_for_update())
    if session and (session.expires_at <= utcnow() or not user.is_active):
        session.status = "expired"; session.stopped_at = utcnow(); session.stop_reason = "expired"; db.commit(); return None
    if session and not session.zone.is_active:
        session.status = "expired"; session.stopped_at = utcnow(); session.stop_reason = "zone_disabled"; db.commit(); return None
    return session


@router.get("/current")
def current(db: Session = Depends(get_db), user: User = Depends(current_user)):
    session = active_for_user(db, user)
    return {"session": public_session(session, session.zone) if session else None}


@router.post("")
def start(body: StartBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not user.is_active: raise ApiError(403, "USER_DISABLED", "This account is disabled.")
    if active_for_user(db, user): raise ApiError(409, "CAPTURE_ALREADY_ACTIVE", "Stop the current capture session before starting another.")
    zone_id = body.zone_id if user.role == UserRole.SUPER_ADMIN else user.zone_id
    if user.role == UserRole.AUTO_SYNC_USER and body.zone_id is not None and body.zone_id != user.zone_id: raise ApiError(403, "ZONE_FORBIDDEN", "Operational users cannot select a different zone.")
    if not zone_id: raise ApiError(400, "ZONE_REQUIRED", "Select a zone before starting capture.")
    zone = db.get(Zone, zone_id)
    if not zone or not zone.is_active: raise ApiError(409, "ZONE_NOT_ACTIVE", "The selected zone is not active.")
    now = utcnow(); session = CaptureSession(user_id=user.id, zone_id=zone.id, status="active", started_at=now, expires_at=now + SESSION_LIFETIME); db.add(session); db.commit(); db.refresh(session)
    return {"session": public_session(session, zone)}


@router.post("/{session_id}/stop")
def stop(session_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    session = db.scalar(select(CaptureSession).where(CaptureSession.id == session_id, CaptureSession.user_id == user.id).with_for_update())
    if not session: raise ApiError(404, "CAPTURE_SESSION_NOT_FOUND", "Capture session not found.")
    if session.status == "active": session.status = "stopped"; session.stopped_at = utcnow(); session.stop_reason = "user_stopped"; db.commit()
    return {"session": public_session(session, session.zone)}
