from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, JSON, String, UniqueConstraint
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
    capture_sessions: Mapped[list[CaptureSession]] = relationship(back_populates="zone")


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
    capture_sessions: Mapped[list[CaptureSession]] = relationship(back_populates="user")


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


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"
    __table_args__ = (Index("ix_admin_audit_events_created", "created_at"), Index("ix_admin_audit_events_target", "target_type", "target_id"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class CaptureSession(Base):
    __tablename__ = "capture_sessions"
    __table_args__ = (CheckConstraint("status IN ('active','stopped','expired')", name="ck_capture_sessions_status"), Index("ix_capture_sessions_user_status", "user_id", "status"), Index("ix_capture_sessions_zone_status", "zone_id", "status"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.id", ondelete="RESTRICT"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    started_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    stopped_at: Mapped[datetime | None] = mapped_column(Timestamp)
    expires_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    user: Mapped[User] = relationship(back_populates="capture_sessions")
    zone: Mapped[Zone] = relationship(back_populates="capture_sessions")
