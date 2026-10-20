from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.auth import COOKIE_NAME, create_session, current_user, cookie_options, login_allowed, normalize_username, record_login_failure, verify_password
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ApiError
from backend.app.core.security import require_local_api_access
from backend.app.db.session import get_db
from backend.app.models.auth import AuthSession, User, utcnow

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(require_local_api_access)])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


def public_user(user: User) -> dict[str, object]:
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role, "zone_id": user.zone_id, "zone_name": user.zone.name if user.zone else None}


@router.post("/login")
def login(body: LoginBody, response: Response, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    key = normalize_username(body.username)
    if not login_allowed(key):
        raise ApiError(429, "LOGIN_THROTTLED", "Too many login attempts. Try again later.")
    user = db.scalar(select(User).where(User.normalized_username == key))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        record_login_failure(key)
        raise ApiError(401, "INVALID_CREDENTIALS", "Username or password is incorrect.")
    token = create_session(db, user)
    response.set_cookie(COOKIE_NAME, token, **cookie_options(settings))
    return {"user": public_user(user)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        from backend.app.core.auth import _token_hash
        session = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
        if session:
            session.revoked_at = utcnow(); db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"user": public_user(user)}
