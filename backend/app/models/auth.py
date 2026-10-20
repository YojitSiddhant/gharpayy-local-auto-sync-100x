from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

Timestamp = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid.uuid4())


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    AUTO_SYNC_USER = "AUTO_SYNC_USER"


class Zone(Base):
    __tablename__ = "zones"
    __table_args__ = (UniqueConstraint("slug", name="uq_zones_slug"), UniqueConstraint("name", name="uq_zones_name"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, onupdate=utcnow)
    users: Mapped[list[User]] = relationship(back_populates="zone")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("normalized_username", name="uq_users_normalized_username"), CheckConstraint("role IN ('SUPER_ADMIN','AUTO_SYNC_USER')", name="ck_users_role"), CheckConstraint("(role = 'SUPER_ADMIN' AND zone_id IS NULL) OR (role = 'AUTO_SYNC_USER' AND zone_id IS NOT NULL)", name="ck_users_role_zone"), Index("ix_users_zone_active", "zone_id", "is_active"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_username: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[UserRole] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    zone_id: Mapped[str | None] = mapped_column(ForeignKey("zones.id", ondelete="RESTRICT"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, onupdate=utcnow)
    password_changed_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    zone: Mapped[Zone | None] = relationship(back_populates="users")
    sessions: Mapped[list[AuthSession]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user_active", "user_id", "revoked_at", "expires_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(Timestamp)
    user: Mapped[User] = relationship(back_populates="sessions")
