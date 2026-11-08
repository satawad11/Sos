from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import (String, Integer, Boolean, DateTime, ForeignKey, Text, JSON, UniqueConstraint, Float, LargeBinary)
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
    must_change: Mapped[bool] = mapped_column(Boolean, default=False)      # temp/initial password: must set a new one first
    token_ver: Mapped[int] = mapped_column(Integer, default=0)             # bump to revoke every issued token
    password_changed_at: Mapped[datetime | None] = _dt(nullable=True)
    last_login_at: Mapped[datetime | None] = _dt(nullable=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)     # 2-step sign-in (app/mfa.py)
    totp_pending: Mapped[str | None] = mapped_column(String(64), nullable=True)    # secret shown during enrolment
    totp_last_step: Mapped[int | None] = mapped_column(Integer, nullable=True)     # replay protection
    totp_enabled_at: Mapped[datetime | None] = _dt(nullable=True)
    backup_codes: Mapped[list | None] = mapped_column(JSON, nullable=True)         # sha256 of unused one-time codes


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
    # PDPA: consent record (health data = sensitive personal data, section 26)
    consent_at: Mapped[datetime | None] = _dt(nullable=True)
    consent_by: Mapped[str] = mapped_column(String(64), default="")        # staff user who recorded it
    consent_note: Mapped[str] = mapped_column(String(128), default="")     # e.g. consent form number / who consented (guardian)
    consent_withdrawn_at: Mapped[datetime | None] = _dt(nullable=True)
    anonymized_at: Mapped[datetime | None] = _dt(nullable=True)


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
    loc_lat: Mapped[float | None] = mapped_column(Float, nullable=True)    # last GPS fix reported by the device (optional hardware)
    loc_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    loc_at: Mapped[datetime | None] = _dt(nullable=True)
    loc_sats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loc_hdop: Mapped[float | None] = mapped_column(Float, nullable=True)
    watch: Mapped[dict | None] = mapped_column(JSON, nullable=True)       # DeviceMonitor state (offline/battery/mains alerts)
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
    msg_seq: Mapped[int] = mapped_column(Integer, default=0)                 # last MSG sequence number used for this case
    msg_pending: Mapped[dict | None] = mapped_column(JSON, nullable=True)    # {seq, code, value, by} not yet shown on the device
    msg_sent_at: Mapped[datetime | None] = _dt(nullable=True)               # last time the pending MSG was queued
    loc_lat: Mapped[float | None] = mapped_column(Float, nullable=True)    # live fix received for THIS alert (LOC message)
    loc_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    loc_at: Mapped[datetime | None] = _dt(nullable=True)
    loc_sats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loc_hdop: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    last_seen: Mapped[datetime | None] = _dt(nullable=True)        # last time we heard from it by ANY route (report or device uplink)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)    # False = retired / do not alert
    state: Mapped[str] = mapped_column(String(8), default="unknown")  # unknown|online|offline (what we last told people)
    state_changed_at: Mapped[datetime | None] = _dt(nullable=True)
    offline_reason: Mapped[str] = mapped_column(String(8), default="")  # silent|radio
    offline_notified_at: Mapped[datetime | None] = _dt(nullable=True)
    last_report_at: Mapped[datetime | None] = _dt(nullable=True)   # last health report from the gateway itself
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # sanitized last health report
    degraded_since: Mapped[datetime | None] = _dt(nullable=True)   # radio/forwarder reported down since...
    uplinks_total: Mapped[int] = mapped_column(Integer, default=0)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)        # registered (installed) position
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_lat: Mapped[float | None] = mapped_column(Float, nullable=True)    # last fix reported by the gateway's own GPS (optional)
    gps_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_at: Mapped[datetime | None] = _dt(nullable=True)
    gps_sats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")                    # free text for staff (where it is mounted, who to call...)
    offline_min: Mapped[int | None] = mapped_column(Integer, nullable=True)  # per-gateway override of offline.gateway_offline_min
    alert_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)      # per-gateway override of the system-alert recipients


class GatewayCommand(Base):
    """A remote action asked of a gateway's agent (signed MQTT message); the agent answers on <prefix>/gateway/<id>/sos/cmd-result."""
    __tablename__ = "sos_gateway_commands"
    id: Mapped[int] = mapped_column(primary_key=True)
    cmd_id: Mapped[str] = mapped_column(String(16), unique=True)           # random 16 hex, signed + echoed by the agent
    gateway_id: Mapped[str] = mapped_column(String(16), index=True)
    action: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(10), default="pending")     # pending | ok | failed | expired
    detail: Mapped[str] = mapped_column(String(240), default="")
    output: Mapped[str] = mapped_column(Text, default="")                  # e.g. log tail (sanitised, size-capped)
    requested_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = _dt(default=utcnow)
    done_at: Mapped[datetime | None] = _dt(nullable=True)


class PushSub(Base):
    """A browser/phone that asked for Web Push notifications (app/push.py)."""
    __tablename__ = "sos_push_subs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("sos_users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(String(1000), unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(120), default="")       # e.g. "Android · Chrome"
    created_at: Mapped[datetime] = _dt(default=utcnow)
    last_ok_at: Mapped[datetime | None] = _dt(nullable=True)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)


class Secret(Base):
    """Server-side secrets generated at runtime (e.g. VAPID key). Never exposed through the API."""
    __tablename__ = "sos_secrets"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Asset(Base):
    """Small uploaded files (organisation logos for the PDF report)."""
    __tablename__ = "sos_assets"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    mime: Mapped[str] = mapped_column(String(32))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = _dt(default=utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), default="")
