from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import (String, Integer, Boolean, DateTime, ForeignKey, Text, JSON, UniqueConstraint, Float)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TZDateTime(DateTime):
    """Always-UTC aware datetime (sqlite drops tzinfo; we re-attach on read)."""
    pass


def _dt(**kw):
    return mapped_column(DateTime(timezone=True), **kw)


class User(Base):
    __tablename__ = "sos_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(16), default="staff")  # admin | staff | viewer
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = _dt(nullable=True)


class Patient(Base):
    __tablename__ = "sos_patients"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(128))
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    address: Mapped[str] = mapped_column(Text, default="")
    village: Mapped[str] = mapped_column(String(64), default="")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    phone: Mapped[str] = mapped_column(String(32), default="")
    notes: Mapped[str] = mapped_column(Text, default="")  # keep minimal: PDPA data minimisation
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _dt(default=utcnow)


class Device(Base):
    __tablename__ = "sos_devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    dev_eui: Mapped[str] = mapped_column(String(16), unique=True)  # lowercase hex
    label: Mapped[str] = mapped_column(String(64), default="")
    lifecycle: Mapped[str] = mapped_column(String(16), default="ready")  # ready|active|repair|retired|lost
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("sos_patients.id"), nullable=True)
    fw_version: Mapped[str] = mapped_column(String(16), default="")
    config_ver: Mapped[int] = mapped_column(Integer, default=1)  # desired config version (1..255)
    config_override: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # per-device overrides of device_defaults
    reported_config_ver: Mapped[int | None] = mapped_column(Integer, nullable=True)  # last cfg ver the device reported
    config_changed_at: Mapped[datetime | None] = _dt(nullable=True)
    config_applied_at: Mapped[datetime | None] = _dt(nullable=True)
    last_seen: Mapped[datetime | None] = _dt(nullable=True)
    battery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mains_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_rssi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_snr: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_gateway: Mapped[str] = mapped_column(String(16), default="")
    patient = relationship(Patient)


class Caregiver(Base):
    __tablename__ = "sos_caregivers"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32), default="อสม.")
    phone: Mapped[str] = mapped_column(String(32), default="")
    line_user_id: Mapped[str] = mapped_column(String(64), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Assignment(Base):
    __tablename__ = "sos_assignments"
    __table_args__ = (UniqueConstraint("patient_id", "priority"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("sos_patients.id"))
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("sos_caregivers.id"))
    priority: Mapped[int] = mapped_column(Integer)  # 1,2,...
    caregiver = relationship(Caregiver)


class Case(Base):
    __tablename__ = "sos_cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("sos_devices.id"))
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("sos_patients.id"), nullable=True)
    alert_id: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(12), default="new")  # new|accepted|closed|cancelled
    overdue: Mapped[bool] = mapped_column(Boolean, default=False)
    after_hours: Mapped[bool] = mapped_column(Boolean, default=False)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _dt(default=utcnow)
    accepted_at: Mapped[datetime | None] = _dt(nullable=True)
    accepted_by: Mapped[str] = mapped_column(String(128), default="")
    closed_at: Mapped[datetime | None] = _dt(nullable=True)
    close_reason: Mapped[str] = mapped_column(String(32), default="")  # resolved|false_alarm
    note: Mapped[str] = mapped_column(Text, default="")
    esc_step: Mapped[int] = mapped_column(Integer, default=0)
    next_escalation_at: Mapped[datetime | None] = _dt(nullable=True)
    ack_result_sent: Mapped[int] = mapped_column(Integer, default=0)
    ack_sent_at: Mapped[datetime | None] = _dt(nullable=True)
    device = relationship(Device)
    patient = relationship(Patient)
    events = relationship("CaseEvent", order_by="CaseEvent.id", back_populates="case")


class CaseEvent(Base):
    __tablename__ = "sos_case_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("sos_cases.id"), index=True)
    at: Mapped[datetime] = _dt(default=utcnow)
    kind: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(128), default="system")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    case = relationship(Case, back_populates="events")


class Setting(Base):
    __tablename__ = "sos_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = _dt(default=utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), default="")


class Holiday(Base):
    __tablename__ = "sos_holidays"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[str] = mapped_column(String(10), unique=True)  # YYYY-MM-DD (Gregorian)
    name: Mapped[str] = mapped_column(String(128), default="")


class BindCode(Base):
    __tablename__ = "sos_line_bind_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("sos_caregivers.id"))
    expires_at: Mapped[datetime] = _dt()
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Notification(Base):
    __tablename__ = "sos_notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("sos_cases.id"), index=True)
    caregiver_id: Mapped[int | None] = mapped_column(ForeignKey("sos_caregivers.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="line")
    status: Mapped[str] = mapped_column(String(16), default="sent")  # sent|failed|skipped
    detail: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = _dt(default=utcnow)


class AuditLog(Base):
    __tablename__ = "sos_audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = _dt(default=utcnow)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Gateway(Base):
    __tablename__ = "sos_gateways"
    id: Mapped[int] = mapped_column(primary_key=True)
    gateway_id: Mapped[str] = mapped_column(String(16), unique=True)
    label: Mapped[str] = mapped_column(String(64), default="")
    last_seen: Mapped[datetime | None] = _dt(nullable=True)
