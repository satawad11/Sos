"""Who is told what on LINE — one place for the rules.

A recipient is always a Caregiver row (that is what the LINE binding, the outbox and the webhook work with). A signed-in
user of the dashboard who wants LINE alerts gets a linked row of their own (Caregiver.user_id): a "contact". The four
kinds of alert and where each switch is stored:

  system   gateways, server, backups                 system_alert.caregiver_ids (setting)      admins only
  device   a patient's button is silent/low/unplugged Caregiver.alert_device                    admin, staff
  cases    every patient alert, from the first press  Caregiver.is_central                      admin, staff
  overdue  a patient alert nobody accepted in time    Caregiver.alert_overdue                   admin, staff

The patient's own caregivers (Assignment) are told about their patients whatever these switches say.
"""
from __future__ import annotations
from sqlalchemy.orm import Session
from . import settings_store
from .models import Caregiver, User

ROLE_ALLOW = {"admin": ("system", "device", "cases"), "staff": ("device", "cases"), "viewer": ()}
CASES = ("off", "overdue", "all")      # overdue = only when nobody accepted within escalation.overdue_after_s
ROLE_TITLE = {"admin": "ผู้ดูแลระบบ", "staff": "เจ้าหน้าที่", "viewer": "ผู้ดู"}


class RoutingError(ValueError):
    pass


def _sys_ids(db: Session) -> list[int]:
    return list(settings_store.get(db, "system_alert").get("caregiver_ids") or [])


def _set_system(db: Session, cg: Caregiver, on: bool, actor: str) -> None:
    ids = _sys_ids(db)
    if on == (cg.id in ids):
        return
    cur = settings_store.get(db, "system_alert")
    settings_store.put(db, "system_alert", {**cur, "caregiver_ids": sorted(set(ids) | {cg.id}) if on else [i for i in ids if i != cg.id]}, actor)


def contact(db: Session, user: User, create: bool = False) -> Caregiver | None:
    """The LINE recipient row that belongs to this dashboard user."""
    cg = db.query(Caregiver).filter(Caregiver.user_id == user.id).order_by(Caregiver.id).first()
    if cg is None and create:
        cg = Caregiver(full_name=(user.display_name or user.username)[:128], role=ROLE_TITLE.get(user.role, "เจ้าหน้าที่"),
                       user_id=user.id, active=bool(user.active))
        db.add(cg); db.flush()
    return cg


def prefs(db: Session, cg: Caregiver | None) -> dict:
    if cg is None:
        return {"system": False, "device": False, "cases": "off"}
    return {"system": cg.id in _sys_ids(db), "device": bool(cg.alert_device),
            "cases": "all" if cg.is_central else "overdue" if cg.alert_overdue else "off"}


def state(db: Session, user: User) -> dict:
    cg = contact(db, user)
    return {"allowed": list(ROLE_ALLOW.get(user.role, ())), "bound": bool(cg and cg.line_user_id), "contact_id": cg.id if cg else None,
            "prefs": prefs(db, cg)}


def set_prefs(db: Session, user: User, want: dict, actor: str) -> dict:
    """Change what one user is told. Only what the user's role allows can be switched ON; anything can be switched off."""
    allow = ROLE_ALLOW.get(user.role, ())
    cur = prefs(db, contact(db, user))
    new = {**cur, **{k: v for k, v in want.items() if v is not None}}
    if new["cases"] not in CASES:
        raise RoutingError("cases must be off, overdue or all")
    for k in ("system", "device"):
        if not isinstance(new[k], bool):
            raise RoutingError(f"{k} must be true/false")
        if new[k] and k not in allow:
            raise RoutingError("บทบาทของผู้ใช้นี้ไม่มีสิทธิ์รับการแจ้งเตือนประเภทนี้")
    if new["cases"] != "off" and "cases" not in allow:
        raise RoutingError("บทบาทของผู้ใช้นี้ไม่มีสิทธิ์รับการแจ้งเตือนประเภทนี้")
    if new == cur:
        return new
    cg = contact(db, user, create=True)
    cg.alert_device = new["device"]
    cg.is_central, cg.alert_overdue = new["cases"] == "all", new["cases"] == "overdue"
    _set_system(db, cg, new["system"], actor)
    return new


def sync_user(db: Session, user: User, actor: str = "system", deleted: bool = False) -> None:
    """Keep the contact in step with the account: a closed/deleted account is told nothing, a role that lost a right loses
    the matching alert, and the name follows the display name."""
    cg = contact(db, user)
    if cg is None:
        return
    if deleted or not user.active:
        cg.active = False
        if deleted:
            cg.is_central = cg.alert_device = cg.alert_overdue = False
            cg.line_user_id, cg.user_id = "", None
            _set_system(db, cg, False, actor)
        return
    cg.active = True
    cg.full_name, cg.role = (user.display_name or user.username)[:128], ROLE_TITLE.get(user.role, "เจ้าหน้าที่")
    allow = ROLE_ALLOW.get(user.role, ())
    if "system" not in allow:
        _set_system(db, cg, False, actor)
    if "device" not in allow:
        cg.alert_device = False
    if "cases" not in allow:
        cg.is_central = cg.alert_overdue = False


def device_recipients(db: Session, fallback) -> list[Caregiver]:
    """People who asked for button problems. Nobody chose that yet -> the system-alert recipients, as before."""
    out = db.query(Caregiver).filter(Caregiver.alert_device.is_(True), Caregiver.active.is_(True)).order_by(Caregiver.id).all()
    return out or list(fallback(db))


def overdue_recipients(db: Session) -> list[Caregiver]:
    return db.query(Caregiver).filter(Caregiver.alert_overdue.is_(True), Caregiver.active.is_(True)).order_by(Caregiver.id).all()
