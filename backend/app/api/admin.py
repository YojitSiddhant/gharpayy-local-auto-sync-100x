from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.auth import hash_password, normalize_username, require_super_admin, revoke_sessions
from backend.app.core.errors import ApiError
from backend.app.core.security import require_local_api_access
from backend.app.db.session import get_db
from backend.app.models.auth import AdminAuditEvent, User, UserRole, Zone, utcnow

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_local_api_access), Depends(require_super_admin)])


def slugify(value: str) -> str:
    return "-".join(value.strip().casefold().split())


def audit(db: Session, actor: User, action: str, target_type: str, target_id: str, details: dict | None = None) -> None:
    db.add(AdminAuditEvent(actor_user_id=actor.id, action=action, target_type=target_type, target_id=target_id, details=details))


def zone_out(zone: Zone, count: int) -> dict:
    return {"id": zone.id, "name": zone.name, "slug": zone.slug, "is_active": zone.is_active, "user_count": count}


def user_out(user: User) -> dict:
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role, "is_active": user.is_active, "zone": {"id": user.zone.id, "name": user.zone.name, "is_active": user.zone.is_active} if user.zone else None}


class ZoneBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class UserBody(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=12, max_length=256)
    zone_id: str = Field(min_length=36, max_length=36)


class PasswordBody(BaseModel):
    password: str = Field(min_length=12, max_length=256)


class ZoneAssignment(BaseModel):
    zone_id: str = Field(min_length=36, max_length=36)


@router.get("/zones")
def list_zones(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(User.zone_id, func.count(User.id)).where(User.role == UserRole.AUTO_SYNC_USER).group_by(User.zone_id)).all())
    return {"items": [zone_out(zone, int(counts.get(zone.id, 0))) for zone in db.scalars(select(Zone).order_by(Zone.name))]}


@router.post("/zones")
def create_zone(body: ZoneBody, db: Session = Depends(get_db), actor: User = Depends(require_super_admin)):
    name = " ".join(body.name.strip().split()); slug = slugify(name)
    if db.scalar(select(Zone).where((func.lower(Zone.name) == name.casefold()) | (Zone.slug == slug))):
        raise ApiError(409, "ZONE_EXISTS", "A zone with that name already exists.")
    zone = Zone(name=name, slug=slug); db.add(zone); db.flush(); audit(db, actor, "zone.created", "zone", zone.id, {"name": name}); db.commit(); return zone_out(zone, 0)


@router.patch("/zones/{zone_id}")
def update_zone(zone_id: str, body: ZoneBody, db: Session = Depends(get_db), actor: User = Depends(require_super_admin)):
    zone = db.get(Zone, zone_id)
    if not zone: raise ApiError(404, "ZONE_NOT_FOUND", "Zone not found.")
    name = " ".join(body.name.strip().split()); slug = slugify(name)
    if db.scalar(select(Zone).where(Zone.id != zone.id, (func.lower(Zone.name) == name.casefold()) | (Zone.slug == slug))): raise ApiError(409, "ZONE_EXISTS", "A zone with that name already exists.")
    old = zone.name; zone.name = name; zone.slug = slug; audit(db, actor, "zone.updated", "zone", zone.id, {"old_name": old, "name": name}); db.commit(); return zone_out(zone, 0)


@router.post("/zones/{zone_id}/disable")
@router.post("/zones/{zone_id}/enable")
def set_zone_status(zone_id: str, request: Request, db: Session = Depends(get_db), actor: User = Depends(require_super_admin)):
    zone = db.get(Zone, zone_id)
    if not zone: raise ApiError(404, "ZONE_NOT_FOUND", "Zone not found.")
    zone.is_active = request.url.path.endswith("/enable"); audit(db, actor, "zone.enabled" if zone.is_active else "zone.disabled", "zone", zone.id); db.commit(); return {"id": zone.id, "is_active": zone.is_active}


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    return {"items": [user_out(user) for user in db.scalars(select(User).where(User.role == UserRole.AUTO_SYNC_USER).order_by(User.username))]}


@router.post("/users")
def create_user(body: UserBody, db: Session = Depends(get_db), actor: User = Depends(require_super_admin)):
    normalized = normalize_username(body.username); zone = db.get(Zone, body.zone_id)
    if not zone or not zone.is_active: raise ApiError(409, "ZONE_NOT_ACTIVE", "Users must be assigned to an active zone.")
    if db.scalar(select(User).where(User.normalized_username == normalized)): raise ApiError(409, "USERNAME_EXISTS", "That username already exists.")
    user = User(username=body.username.strip(), normalized_username=normalized, display_name=body.display_name.strip(), password_hash=hash_password(body.password), role=UserRole.AUTO_SYNC_USER, zone_id=zone.id); db.add(user); db.flush(); audit(db, actor, "user.created", "user", user.id, {"zone_id": zone.id}); db.commit(); return user_out(user)


@router.post("/users/{user_id}/disable")
@router.post("/users/{user_id}/enable")
def set_user_status(user_id: str, request: Request, db: Session = Depends(get_db), actor: User = Depends(require_super_admin)):
    user = db.get(User, user_id)
    if not user or user.role != UserRole.AUTO_SYNC_USER: raise ApiError(404, "USER_NOT_FOUND", "Operational user not found.")
    user.is_active = request.url.path.endswith("/enable")
    if not user.is_active: revoke_sessions(db, user.id)
    audit(db, actor, "user.enabled" if user.is_active else "user.disabled", "user", user.id); db.commit(); return user_out(user)


@router.post("/users/{user_id}/password")
def reset_password(user_id: str, body: PasswordBody, db: Session = Depends(get_db), actor: User = Depends(require_super_admin)):
    user = db.get(User, user_id)
    if not user or user.role != UserRole.AUTO_SYNC_USER: raise ApiError(404, "USER_NOT_FOUND", "Operational user not found.")
    user.password_hash = hash_password(body.password); user.password_changed_at = utcnow(); revoke_sessions(db, user.id); audit(db, actor, "user.password_reset", "user", user.id); db.commit(); return user_out(user)


@router.post("/users/{user_id}/zone")
def reassign_zone(user_id: str, body: ZoneAssignment, db: Session = Depends(get_db), actor: User = Depends(require_super_admin)):
    user = db.get(User, user_id); zone = db.get(Zone, body.zone_id)
    if not user or user.role != UserRole.AUTO_SYNC_USER: raise ApiError(404, "USER_NOT_FOUND", "Operational user not found.")
    if not zone or not zone.is_active: raise ApiError(409, "ZONE_NOT_ACTIVE", "Users must be assigned to an active zone.")
    old = user.zone_id; user.zone_id = zone.id; audit(db, actor, "user.zone_reassigned", "user", user.id, {"old_zone_id": old, "new_zone_id": zone.id}); db.commit(); return user_out(user)
