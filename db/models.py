from datetime import datetime
from typing import Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from db.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram User ID
    role: Mapped[str] = mapped_column(String(20), default="unassigned")  # "parent", "child", "unassigned"
    full_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    virtual_balance: Mapped[int] = mapped_column(Integer, default=0)
    coin_rate_coins: Mapped[int] = mapped_column(Integer, default=1000)
    coin_rate_som: Mapped[int] = mapped_column(Integer, default=10000)
    web_access_code: Mapped[Optional[str]] = mapped_column(String(20), unique=True, index=True, nullable=True)
    pairing_code: Mapped[Optional[str]] = mapped_column(String(16), unique=True, index=True, nullable=True)
    pairing_code_created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_sos_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    location_logs: Mapped[list["LocationLog"]] = relationship("LocationLog", back_populates="child", cascade="all, delete-orphan")
    geofences: Mapped[list["Geofence"]] = relationship("Geofence", back_populates="parent", cascade="all, delete-orphan", foreign_keys="Geofence.parent_id")


class ParentChildLink(Base):
    __tablename__ = "parent_child_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    child_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    nickname: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("parent_id", "child_id", name="uq_parent_child"),
    )


class LocationLog(Base):
    __tablename__ = "location_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    child_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    battery_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 0-100%
    is_charging: Mapped[bool] = mapped_column(Boolean, default=False)
    sound_mode: Mapped[Optional[str]] = mapped_column(String(20), default="normal")  # "normal", "vibrate", "silent"
    source: Mapped[str] = mapped_column(String(30), default="telegram_live")  # "telegram_live", "telegram_pin", "api_telemetry"
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    child: Mapped["User"] = relationship("User", back_populates="location_logs", foreign_keys=[child_id])


class Geofence(Base):
    __tablename__ = "geofences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    child_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "Maktab", "Uy", "Sport majmuasi"
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    radius_meters: Mapped[float] = mapped_column(Float, default=200.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    parent: Mapped["User"] = relationship("User", back_populates="geofences", foreign_keys=[parent_id])


class GeofenceState(Base):
    __tablename__ = "geofence_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    geofence_id: Mapped[int] = mapped_column(Integer, ForeignKey("geofences.id", ondelete="CASCADE"), index=True)
    child_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    is_inside: Mapped[bool] = mapped_column(Boolean, default=False)
    last_transition_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("geofence_id", "child_id", name="uq_geofence_child_state"),
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    child_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reward_coins: Mapped[int] = mapped_column(Integer, default=1000)
    status: Mapped[str] = mapped_column(String(30), default="pending")  # "pending", "seen", "scheduled", "completed_by_child", "approved", "rejected"
    
    # Parent media attachment (photo or video)
    media_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # "photo", "video"
    media_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Child response and commitment
    seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    promised_time: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # e.g. "30 daqiqadan keyin"

    # Child completion proof photo
    proof_photo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class BatteryAlertLog(Base):
    __tablename__ = "battery_alert_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    child_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    last_alerted_level: Mapped[int] = mapped_column(Integer)
    last_alerted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
