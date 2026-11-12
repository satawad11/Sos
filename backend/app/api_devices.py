"""Buttons (devices), gateways and their remote commands, and the map.
Split out of api.py; shared helpers and the engine handle still live there."""
from __future__ import annotations
from fastapi import APIRouter
from . import gateways as gwmod, geo, gwcmd, privacy, provision as prov, settings_store
from .auth import admin_only, audit, current_user, staff_or_admin
from .db import get_db
from .engine import aware
from .models import Caregiver, Case, Device, Gateway, GatewayCommand, Patient, utcnow
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload
from .api import EUI_RE, get_engine, iso

router = APIRouter(prefix="/api")

# ---------------- devices ----------------
class DeviceIn(BaseModel):
    dev_eui: str
    label: str = ""
    lifecycle: str = "ready"
    patient_id: int | None = None


@router.get("/devices")
def devices(db: Session = Depends(get_db), u=Depends(current_user)):
    off = settings_store.get(db, "offline")["device_offline_min"]
    gcfg = settings_store.get(db, "geo")
    now = utcnow(); out = []
    for d in db.query(Device).options(joinedload(Device.patient)).order_by(Device.id).all():
        ls = aware(d.last_seen)
        offline = d.lifecycle == "active" and (ls is None or (now - ls).total_seconds() > off * 60)
        pending = (d.reported_config_ver is not None and d.reported_config_ver != (d.config_ver & 0xFF)) \
            or (d.reported_config_ver is None and bool(d.config_override))
        loc = None
        if d.loc_lat is not None and u.role != "viewer":
            home = (round(geo.distance_m(d.patient.lat, d.patient.lon, d.loc_lat, d.loc_lon))
                    if d.patient and d.patient.lat is not None and d.patient.lon is not None else None)
            fresh = (now - aware(d.loc_at)).total_seconds() <= gcfg["gps_fresh_min"] * 60
            loc = {"lat": d.loc_lat, "lon": d.loc_lon, "at": iso(d.loc_at), "sats": d.loc_sats, "hdop": d.loc_hdop, "fresh": fresh,
                   "home_distance_m": home, "away": home is not None and home > gcfg["home_radius_m"],
                   "url": geo.maps_url(d.loc_lat, d.loc_lon)}
        cfg = settings_store.effective_device_cfg(db, d)
        home = ({"lat": d.patient.lat, "lon": d.patient.lon}
                if u.role != "viewer" and d.patient and d.patient.lat is not None and d.patient.lon is not None else None)
        out.append({**settings_store.device_link(db, d, now, cfg, off), "loc": loc, "home": home, "config": cfg, "config_override": d.config_override or {},
                    "config_pending": pending, "config_ver": d.config_ver, "config_reported_ver": d.reported_config_ver,
                    "config_applied_at": iso(d.config_applied_at), "config_changed_at": iso(d.config_changed_at),
                    "id": d.id, "dev_eui": d.dev_eui, "label": d.label, "lifecycle": d.lifecycle,
                    "patient_id": d.patient_id, "patient": d.patient.full_name if d.patient else None,
                    "fw": d.fw_version, "battery": d.battery, "mains_ok": d.mains_ok, "rssi": d.last_rssi,
                    "snr": d.last_snr, "last_seen": iso(d.last_seen), "offline": offline})
    return out


def _require_consent(db: Session, lifecycle: str, patient_id: int | None):
    """PDPA: do not put a button into service for someone whose consent is not on record."""
    if lifecycle != "active" or not patient_id or not settings_store.get(db, "privacy")["require_consent"]:
        return
    p = db.get(Patient, patient_id)
    if p is None: raise HTTPException(422, "patient not found")
    if p.anonymized_at: raise HTTPException(409, "ผู้ป่วยรายนี้ถูกลบข้อมูลแล้ว")
    if not privacy.consent_ok(p):
        raise HTTPException(409, "ผู้ป่วยยังไม่มีบันทึกความยินยอม (PDPA) — บันทึกความยินยอมที่หน้าผู้ป่วยก่อนตั้งเป็น active")


@router.post("/devices")
def add_device(b: DeviceIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    eui = b.dev_eui.lower().replace(":", "")
    if not EUI_RE.match(eui): raise HTTPException(422, "DevEUI must be 16 hex chars")
    if db.query(Device).filter_by(dev_eui=eui).first(): raise HTTPException(409, "already exists")
    _require_consent(db, b.lifecycle, b.patient_id)
    d = Device(dev_eui=eui, label=b.label, lifecycle=b.lifecycle, patient_id=b.patient_id)
    db.add(d); audit(db, u.username, "device_create", eui); db.commit()
    return {"id": d.id, "dev_eui": eui}


@router.put("/devices/{did}")
def edit_device(did: int, b: DeviceIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    d = db.get(Device, did)
    if not d: raise HTTPException(404)
    if b.lifecycle not in ("ready", "active", "repair", "retired", "lost"): raise HTTPException(422, "bad lifecycle")
    if b.patient_id:
        other = db.query(Device).filter(Device.patient_id == b.patient_id, Device.id != did,
                                        Device.lifecycle == "active").first()
        if other and b.lifecycle == "active": raise HTTPException(409, "patient already has an active device")
    _require_consent(db, b.lifecycle, b.patient_id)
    if d.patient_id != b.patient_id:                      # a fix from the old patient's home says nothing about the new one
        d.loc_lat = d.loc_lon = d.loc_at = d.loc_sats = d.loc_hdop = None
    was_retired = d.lifecycle == "retired"
    d.label, d.lifecycle, d.patient_id = b.label, b.lifecycle, b.patient_id
    audit(db, u.username, "device_update", d.dev_eui, lifecycle=b.lifecycle, patient=b.patient_id); db.commit()
    cs_res = "unchanged"
    if was_retired != (b.lifecycle == "retired"):
        cs_res = _cs_device_disabled(db, u.username, d.dev_eui, b.lifecycle == "retired")
    return {"ok": True, "chirpstack": cs_res}


def _cs_device_disabled(db: Session, who: str, eui: str, disabled: bool) -> str:
    """A retired button is disabled in ChirpStack (its uplinks are dropped there); un-retiring enables it again."""
    cs = prov.get_cs()
    if not cs.configured: return "not_configured"
    try:
        changed = cs.set_disabled(eui, disabled)
    except Exception as e:
        audit(db, who, "chirpstack_device_failed", eui, err=str(e)[:150]); db.commit()
        return "error: " + str(e)[:150]
    if changed:
        audit(db, who, "chirpstack_device_disabled" if disabled else "chirpstack_device_enabled", eui); db.commit()
    return "disabled" if disabled else "enabled"


@router.delete("/devices/{did}")
def delete_device(did: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    d = db.get(Device, did)
    if not d: raise HTTPException(404)
    if db.query(Case).filter_by(device_id=did).first():
        raise HTTPException(409, "อุปกรณ์นี้มีประวัติเคสแล้ว ลบไม่ได้ — เปลี่ยนสถานะเป็น 'ปลดระวาง/สูญหาย' แทน")
    eui = d.dev_eui; db.delete(d); audit(db, u.username, "device_delete", eui); db.commit()
    return {"ok": True, "chirpstack": _cs_remove(db, u.username, "device", eui)}


def _cs_remove(db: Session, who: str, kind: str, ident: str) -> str:
    """Best effort: also remove the device/gateway from ChirpStack. Never fails the delete here (the row is already gone)."""
    cs = prov.get_cs()
    if not cs.configured: return "not_configured"
    try:
        gone = cs.remove_device(ident) if kind == "device" else cs.remove_gateway(ident)
    except Exception as e:
        audit(db, who, f"chirpstack_{kind}_delete_failed", ident, err=str(e)[:150]); db.commit()
        return "error: " + str(e)[:150]
    if gone: audit(db, who, f"chirpstack_{kind}_delete", ident); db.commit()
    return "deleted" if gone else "absent"


class DeviceConfigIn(BaseModel):
    override: dict | None = None      # {} or None = back to the shared defaults


def bump_ver(d: Device) -> None:
    d.config_ver = (d.config_ver % 255) + 1
    d.config_changed_at = utcnow()


@router.put("/devices/{did}/config")
def set_device_config(did: int, b: DeviceConfigIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    d = db.get(Device, did)
    if not d: raise HTTPException(404)
    off = settings_store.get(db, "offline")["device_offline_min"]
    try:
        ov = settings_store.validate_device_cfg(b.override or {}, partial=True, offline_min=off)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if (d.config_override or {}) == ov:
        return {"ok": True, "changed": False}
    d.config_override = ov or None
    bump_ver(d)
    audit(db, u.username, "device_config", d.dev_eui, override=ov, config_ver=d.config_ver); db.commit()
    return {"ok": True, "changed": True, "config_ver": d.config_ver,
            "effective": settings_store.effective_device_cfg(db, d)}


def gw_dict(g: Gateway, now, off: int, n_dev: dict, move_m: int = 200, role: str = "staff", heard: dict | None = None) -> dict:
    st, reason = gwmod.classify(g, now, g.offline_min or off, move_m)
    crit, warn = gwmod.critical(g.report), gwmod.warnings(g.report)
    mv = gwmod.moved_m(g, now, move_m)
    if mv is not None: warn = warn + [f"ตำแหน่ง GPS ห่างจากที่ลงทะเบียน {mv} เมตร (เกตเวย์อาจถูกย้ายหรือถูกนำออกไป)"]
    gps_fresh = bool(g.gps_at and (now - aware(g.gps_at)).total_seconds() <= gwmod.GPS_FRESH_S)
    rep = dict(g.report or {})
    loc = {"registered": {"lat": g.lat, "lon": g.lon} if g.lat is not None else None,
           "gps": {"lat": g.gps_lat, "lon": g.gps_lon, "at": iso(g.gps_at), "sats": g.gps_sats, "fresh": gps_fresh} if g.gps_lat is not None else None,
           "gps_attached": "gps" in rep, "moved_m": mv,
           "url": geo.maps_url(g.lat, g.lon) if g.lat is not None else None}
    return {"id": g.id, "gateway_id": g.gateway_id, "label": g.label, "enabled": g.enabled is not False,
            "status": st, "offline": st == "offline", "reason": reason or (g.offline_reason if st == "offline" else ""),
            "last_seen": iso(g.last_seen), "last_report_at": iso(g.last_report_at),
            "state_changed_at": iso(g.state_changed_at), "uplinks_total": g.uplinks_total or 0,
            "devices": n_dev.get(g.gateway_id, 0), "devices_heard": (heard or {}).get(g.gateway_id, 0), "report": g.report or {}, "critical": crit, "warnings": warn, "loc": loc,
            "notes": g.notes or "", "offline_min": g.offline_min, "offline_min_effective": g.offline_min or off,
            "alert_ids": g.alert_ids, "alerts_muted": bool(g.alerts_muted), "agent": rep.get("agent") is True,
            "maint": {"since": iso(g.maint_since), "until": iso(g.maint_until), "end": g.maint_end or "online",
                      "reason": g.maint_reason or "", "by": g.maint_by or ""} if g.alerts_muted else None}


@router.get("/gateways/{gid}/impact")
def gateway_impact(gid: int, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    """Patient buttons received by this gateway in the last 7 days, and which of them no other working gateway heard —
    those are the people to call while it is down / in maintenance."""
    g = db.get(Gateway, gid)
    if not g: raise HTTPException(404)
    now, me = utcnow(), g.gateway_id.lower()
    off = settings_store.get(db, "offline")["gateway_offline_min"]
    up = {x.gateway_id.lower() for x in db.query(Gateway).all()
          if x.gateway_id.lower() != me and gwmod.classify(x, now, x.offline_min or off)[0] in ("online", "warning")}
    rows = []
    for d in (db.query(Device).options(joinedload(Device.patient)).filter(Device.lifecycle == "active")
              .order_by(Device.label).all()):
        hb = {k.lower() for k, v in (d.heard_by or {}).items() if now - aware(datetime.fromisoformat(v)) < timedelta(days=7)}
        if not hb and d.last_gateway: hb = {d.last_gateway.lower()}          # older data: only the last gateway is known
        if me not in hb: continue
        others = sorted(hb & up)
        p = d.patient if d.patient and not d.patient.anonymized_at else None
        rows.append({"device_id": d.id, "label": d.label or d.dev_eui, "dev_eui": d.dev_eui, "last_seen": iso(d.last_seen),
                     "only_here": not others, "other_gateways": others,
                     "patient": {"id": p.id, "full_name": p.full_name, "phone": p.phone, "village": p.village} if p else None})
    rows.sort(key=lambda r: (not r["only_here"], r["label"]))
    return {"gateway_id": g.gateway_id, "total": len(rows), "only_here": sum(r["only_here"] for r in rows), "devices": rows}


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


@router.put("/patients/{pid}/location")
def set_patient_location(pid: int, b: LocationIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    """Pin a patient's home on the map (only the coordinates change)."""
    p = db.get(Patient, pid)
    if not p: raise HTTPException(404)
    if p.anonymized_at: raise HTTPException(409, "ข้อมูลผู้ป่วยรายนี้ถูกลบ (ทำให้ไม่ระบุตัวตน) แล้ว แก้ไขไม่ได้")
    p.lat, p.lon = round(b.lat, 6), round(b.lon, 6)
    audit(db, u.username, "patient_location", str(pid)); db.commit()
    return {"id": p.id, "lat": p.lat, "lon": p.lon}


@router.get("/gateways")
def gateways(db: Session = Depends(get_db), u=Depends(current_user)):
    off = settings_store.get(db, "offline")["gateway_offline_min"]
    now = utcnow()
    n_dev: dict[str, int] = {}; heard: dict[str, int] = {}     # active devices / every non-retired device last heard here
    for d in db.query(Device).filter(Device.lifecycle != "retired").all():
        if d.last_gateway:
            heard[d.last_gateway] = heard.get(d.last_gateway, 0) + 1
            if d.lifecycle == "active": n_dev[d.last_gateway] = n_dev.get(d.last_gateway, 0) + 1
    mv = settings_store.get(db, "geo")["gateway_move_m"]
    return [gw_dict(g, now, off, n_dev, mv, u.role, heard) for g in db.query(Gateway).order_by(Gateway.id).all()]


def _quality(rssi, snr):
    """Rough LoRa link grade from the last uplink (SF9-ish): good / fair / weak / None when unknown."""
    if rssi is None: return None
    if rssi >= -105 and (snr is None or snr >= 0): return "good"
    if rssi >= -115 and (snr is None or snr >= -7): return "fair"
    return "weak"


@router.get("/map")
def map_data(db: Session = Depends(get_db), u=Depends(current_user)):
    """Gateways, devices and which gateway heard each device last — for the dashboard map.
    Patient/home coordinates and GPS fixes are never sent to the read-only role (PDPA); the connectivity table still is."""
    now = utcnow(); viewer = u.role == "viewer"
    goff = settings_store.get(db, "offline")["gateway_offline_min"]
    doff = settings_store.get(db, "offline")["device_offline_min"]
    gcfg = settings_store.get(db, "geo")
    n_dev: dict[str, int] = {}; heard: dict[str, int] = {}
    devs = db.query(Device).filter(Device.lifecycle != "retired").order_by(Device.id).all()
    for d in devs:
        if d.last_gateway: heard[d.last_gateway] = heard.get(d.last_gateway, 0) + 1
        if d.lifecycle == "active" and d.last_gateway: n_dev[d.last_gateway] = n_dev.get(d.last_gateway, 0) + 1
    gws, gpos = [], {}
    for g in db.query(Gateway).filter(Gateway.enabled.isnot(False)).order_by(Gateway.id).all():
        x = gw_dict(g, now, goff, n_dev, gcfg["gateway_move_m"], u.role, heard)
        loc = x["loc"]; pos = loc["registered"] or (loc["gps"] if loc["gps"] and loc["gps"]["fresh"] else None)
        src = "registered" if loc["registered"] else ("gps" if pos else None)
        if pos: gpos[g.gateway_id] = (pos["lat"], pos["lon"])
        gws.append({"gateway_id": g.gateway_id, "label": g.label, "status": x["status"], "offline": x["offline"], "reason": x["reason"],
                    "maint": x["maint"] is not None,
                    "last_seen": x["last_seen"], "devices": x["devices"], "lat": pos["lat"] if pos else None,
                    "lon": pos["lon"] if pos else None, "pos_src": src})
    dvs, links = [], []
    open_ = {c.patient_id: c for c in db.query(Case).filter(Case.state.in_(("new", "accepted")), Case.is_test.is_(False))
             .order_by(Case.id).all() if c.patient_id}
    for d in devs:
        ls = aware(d.last_seen)
        offline = d.lifecycle == "active" and (ls is None or (now - ls).total_seconds() > doff * 60)
        lat = lon = src = None; home = None
        if not viewer:
            fresh = d.loc_lat is not None and d.loc_at is not None and (now - aware(d.loc_at)).total_seconds() <= gcfg["gps_fresh_min"] * 60
            if fresh: lat, lon, src = d.loc_lat, d.loc_lon, "gps"
            elif d.patient and d.patient.lat is not None and d.patient.lon is not None: lat, lon, src = d.patient.lat, d.patient.lon, "home"
            elif d.loc_lat is not None: lat, lon, src = d.loc_lat, d.loc_lon, "gps_old"
        sig = d.sig or {}
        q = sig.get("q") if sig.get("q") in ("good", "fair", "weak", "bad", "unknown") else _quality(d.last_rssi, d.last_snr)
        cs = open_.get(d.patient_id) if d.patient_id else None
        dvs.append({"id": d.id, "dev_eui": d.dev_eui, "label": d.label, "lifecycle": d.lifecycle,
                    "patient": d.patient.full_name if d.patient else None, "patient_id": d.patient_id,
                    "phone": d.patient.phone if d.patient and not viewer else "", "address": (f"{d.patient.address} {d.patient.village}".strip() if d.patient and not viewer else ""),
                    "case": {"id": cs.id, "state": cs.state, "overdue": cs.overdue, "created_at": iso(cs.created_at)} if cs else None, "battery": d.battery, "mains_ok": d.mains_ok,
                    "offline": offline, "last_seen": iso(d.last_seen), "rssi": d.last_rssi, "snr": d.last_snr, "quality": q,
                    "link_margin": sig.get("margin"), "loss": sig.get("loss"),
                    "gateway_id": d.last_gateway or None, "lat": lat, "lon": lon, "pos_src": src})
        if d.last_gateway:
            gp = gpos.get(d.last_gateway)
            dist = round(geo.distance_m(lat, lon, gp[0], gp[1])) if lat is not None and gp else None
            links.append({"device_id": d.id, "gateway_id": d.last_gateway, "rssi": d.last_rssi, "snr": d.last_snr, "quality": q,
                          "link_margin": sig.get("margin"), "loss": sig.get("loss"),
                          "offline": offline, "distance_m": dist})
    # patients for the "pin a home" tool and homes that have no button yet (never for the read-only role)
    homes, no_loc = [], []
    if not viewer:
        with_dev = {d.patient_id for d in devs if d.patient_id}
        for p in db.query(Patient).filter(Patient.active.is_(True), Patient.anonymized_at.is_(None)).order_by(Patient.id).all():
            row = {"id": p.id, "name": p.full_name, "village": p.village, "address": p.address, "phone": p.phone, "lat": p.lat, "lon": p.lon}
            if p.lat is None: no_loc.append(row)
            elif p.id not in with_dev: homes.append(row)
    return {"gateways": gws, "devices": dvs, "links": links, "restricted": viewer, "coverage_m": gcfg["coverage_m"],
            "homes": homes, "no_location": no_loc}


class GatewayIn(BaseModel):
    gateway_id: str
    label: str = Field(default="", max_length=64)


class MaintIn(BaseModel):
    on: bool
    end: str = "online"                 # online (ends when the gateway is back) | time (until) | manual
    until: datetime | None = None
    reason: str = Field(default="", max_length=200)


class GatewayPatch(BaseModel):
    label: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    lat: float | None = None
    lon: float | None = None
    clear_location: bool = False        # forget the registered position
    use_gps: bool = False               # copy the gateway's own latest GPS fix into the registered position
    notes: str | None = Field(default=None, max_length=500)
    offline_min: int | None = None      # per-gateway "silent for N minutes = offline" (3-240); admin only
    reset_offline: bool = False         # back to the global value
    alert_ids: list[int] | None = None  # per-gateway LINE recipients for this gateway's alerts; admin only
    reset_alerts: bool = False          # back to the global list
    alerts_muted: bool | None = None    # legacy switch: True = maintenance until it is back online, False = end maintenance
    maintenance: MaintIn | None = None


@router.post("/gateways")
def add_gateway(b: GatewayIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    gid = b.gateway_id.lower().replace(":", "").strip()
    if not gwmod.GW_RE.match(gid): raise HTTPException(422, "Gateway ID must be 16 hex chars")
    if db.query(Gateway).filter_by(gateway_id=gid).first(): raise HTTPException(409, "already registered")
    g = Gateway(gateway_id=gid, label=b.label.strip(), enabled=True, state="unknown", uplinks_total=0)
    db.add(g); audit(db, u.username, "gateway_add", gid); db.commit()
    cs_res = _cs_gateway(db, u.username, g)
    return {"ok": True, "id": g.id, "chirpstack": cs_res}


def _cs_gateway(db: Session, who: str, g: Gateway) -> str:
    """Best effort: also register the gateway in ChirpStack so it shows up there (stats, live frames)."""
    cs = prov.get_cs()
    if not cs.configured: return "not_configured"
    try:
        res = cs.ensure_gateway(g.gateway_id, g.label, g.lat, g.lon)
    except Exception as e:
        audit(db, who, "chirpstack_gateway_failed", g.gateway_id, err=str(e)[:150]); db.commit()
        return "error: " + str(e)[:150]
    if res == "created":
        audit(db, who, "chirpstack_gateway_add", g.gateway_id); db.commit()
    return res


@router.delete("/gateways/{gid}")
def delete_gateway(gid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    g = db.get(Gateway, gid)
    if not g: raise HTTPException(404)
    gw = g.gateway_id
    db.query(GatewayCommand).filter_by(gateway_id=gw).delete()
    db.delete(g); audit(db, u.username, "gateway_delete", gw); db.commit()
    return {"ok": True, "chirpstack": _cs_remove(db, u.username, "gateway", gw), "note": "ถ้าเกตเวย์ยังเปิดอยู่ มันจะถูกเพิ่มกลับเองเมื่อส่งข้อมูลครั้งถัดไป — ปิดการเฝ้าระวังแทนถ้าแค่ไม่ต้องการแจ้งเตือน"}


@router.put("/gateways/{gid}")
def patch_gateway(gid: int, b: GatewayPatch, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    g = db.get(Gateway, gid)
    if not g: raise HTTPException(404)
    if b.label is not None:
        g.label = b.label.strip()
    if b.enabled is not None and b.enabled != (g.enabled is not False):
        if u.role != "admin": raise HTTPException(403, "only admin can switch monitoring on/off")
        g.enabled = b.enabled
        if not b.enabled:
            g.state, g.offline_notified_at = "unknown", None     # retired: stop alerting and forget the offline episode
            if g.alerts_muted: get_engine().gw.end_maint(db, g, u.username, "manual", utcnow(), False)
    if b.use_gps:
        if g.gps_lat is None: raise HTTPException(409, "เกตเวย์นี้ยังไม่เคยส่งพิกัด GPS")
        g.lat, g.lon = g.gps_lat, g.gps_lon
    elif b.clear_location:
        g.lat = g.lon = None
    elif b.lat is not None or b.lon is not None:
        if not geo.valid_latlon(b.lat, b.lon): raise HTTPException(422, "lat/lon ไม่ถูกต้อง (ต้องระบุทั้งคู่ และไม่ใช่ 0,0)")
        g.lat, g.lon = round(b.lat, 6), round(b.lon, 6)
    if b.notes is not None:
        g.notes = b.notes.strip()
    if b.offline_min is not None or b.reset_offline or b.alert_ids is not None or b.reset_alerts:
        if u.role != "admin": raise HTTPException(403, "only admin can change alert thresholds/recipients")
        if b.reset_offline: g.offline_min = None
        elif b.offline_min is not None:
            if isinstance(b.offline_min, bool) or not 3 <= b.offline_min <= 240:
                raise HTTPException(422, "offline_min must be 3-240 (gateways report every minute)")
            g.offline_min = b.offline_min
        if b.reset_alerts: g.alert_ids = None
        elif b.alert_ids is not None:
            ids = sorted(set(b.alert_ids))
            if len(ids) > 20: raise HTTPException(422, "too many recipients")
            if ids and {c.id for c in db.query(Caregiver).filter(Caregiver.id.in_(ids)).all()} != set(ids):
                raise HTTPException(422, "unknown caregiver id")
            g.alert_ids = ids or None
    m = b.maintenance
    if m is None and b.alerts_muted is not None:
        m = MaintIn(on=b.alerts_muted)
    if m is not None:
        mon, now = get_engine().gw, utcnow()
        if m.on:
            if g.enabled is False: raise HTTPException(409, "เกตเวย์นี้ปิดการเฝ้าระวังอยู่")
            if m.end not in ("online", "time", "manual"): raise HTTPException(422, "end must be online, time or manual")
            until = None
            if m.end == "time":
                until = aware(m.until) if m.until else None
                if until is None or until <= now + timedelta(minutes=4) or until > now + timedelta(days=14):
                    raise HTTPException(422, "เวลาสิ้นสุดต้องอยู่ระหว่าง 5 นาทีถึง 14 วันจากตอนนี้")
            mon.start_maint(db, g, u.username, m.end, until, m.reason.strip(), now)
        else:
            sa = settings_store.get(db, "system_alert")
            mon.end_maint(db, g, u.username, "manual", now, sa.get("gateway_line", True) is not False)
    audit(db, u.username, "gateway_update", g.gateway_id, label=g.label, enabled=g.enabled is not False,
          located=g.lat is not None, offline_min=g.offline_min, own_recipients=g.alert_ids is not None)
    db.commit()
    return {"ok": True}


# ---- remote actions (signed MQTT commands to the gateway agent) ----
def _cmd_dict(c) -> dict:
    return {"id": c.id, "cmd_id": c.cmd_id, "action": c.action, "label": gwcmd.ACTIONS.get(c.action, c.action), "status": c.status,
            "detail": c.detail, "output": c.output, "by": c.requested_by, "created_at": iso(c.created_at), "done_at": iso(c.done_at)}


def _expire_commands(db: Session, gateway_id: str | None = None):
    q = db.query(GatewayCommand).filter(GatewayCommand.status == "pending")
    if gateway_id: q = q.filter(GatewayCommand.gateway_id == gateway_id)
    now = utcnow(); n = 0
    for c in q.all():
        if (now - aware(c.created_at)).total_seconds() > gwcmd.EXPIRE_S:
            c.status, c.detail, c.done_at = "expired", "ไม่มีคำตอบจากเกตเวย์ (ออฟไลน์ หรือยังไม่ได้เปิดตัวช่วยควบคุม)", now; n += 1
    if n: db.commit()


class GwCommandIn(BaseModel):
    action: str


@router.get("/gateways/{gid}/commands")
def gateway_commands(gid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    g = db.get(Gateway, gid)
    if not g: raise HTTPException(404)
    _expire_commands(db, g.gateway_id)
    rows = db.query(GatewayCommand).filter_by(gateway_id=g.gateway_id).order_by(GatewayCommand.id.desc()).limit(10).all()
    return {"actions": gwcmd.ACTIONS, "agent": (g.report or {}).get("agent") is True, "commands": [_cmd_dict(c) for c in rows]}


@router.post("/gateways/{gid}/commands")
def gateway_command(gid: int, b: GwCommandIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    g = db.get(Gateway, gid)
    if not g: raise HTTPException(404)
    if b.action not in gwcmd.ACTIONS: raise HTTPException(422, "unknown action")
    if (g.report or {}).get("agent") is not True:
        raise HTTPException(409, "เกตเวย์นี้ยังไม่ได้ติดตั้ง/เปิด sos-gateway-agent (ดูคู่มือ docs/GATEWAY-REMOTE.md)")
    _expire_commands(db, g.gateway_id)
    if db.query(GatewayCommand).filter_by(gateway_id=g.gateway_id, status="pending").first():
        raise HTTPException(409, "มีคำสั่งที่ยังรอคำตอบอยู่ รอสักครู่")
    eng = get_engine()
    if eng.gw_cmd_publish is None or not eng.mqtt_ok(): raise HTTPException(503, "ระบบยังเชื่อมต่อ MQTT ไม่ได้")
    cid, payload = gwcmd.make_command(g.gateway_id, b.action)
    c = GatewayCommand(cmd_id=cid, gateway_id=g.gateway_id, action=b.action, requested_by=u.username)
    db.add(c); db.flush()
    if not eng.gw_cmd_publish(g.gateway_id, payload):
        db.rollback(); raise HTTPException(503, "ส่งคำสั่งไม่สำเร็จ (MQTT)")
    audit(db, u.username, "gateway_command", g.gateway_id, cmd=b.action, cmd_id=cid)
    db.commit()
    return _cmd_dict(c)


@router.get("/gateways/{gid}/agent-key")
def gateway_agent_key(gid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    """The per-gateway signing key to put in /etc/sos-gateway.env on the Pi. Shown to admins only and audited."""
    g = db.get(Gateway, gid)
    if not g: raise HTTPException(404)
    audit(db, u.username, "gateway_agent_key_view", g.gateway_id); db.commit()
    return {"gateway_id": g.gateway_id, "key": gwcmd.agent_key(g.gateway_id)}


@router.post("/system-alert/test")
def system_alert_test(db: Session = Depends(get_db), u=Depends(admin_only)):
    """Send a test LINE message to the people who receive 'gateway offline' alerts."""
    mon = get_engine().gw
    if not mon.recipients(db):
        raise HTTPException(422, "ยังไม่ได้เลือกผู้รับการแจ้งเตือนระบบ (หรือผู้รับสุดท้ายใน escalation)")
    res = mon.send_system(db, "✅ ข้อความทดสอบจากระบบแจ้งเหตุฉุกเฉิน: ถ้าคุณเห็นข้อความนี้ แสดงว่าจะได้รับการแจ้งเตือนเมื่อเกตเวย์ขาดการเชื่อมต่อ")
    audit(db, u.username, "system_alert_test", "", results=res); db.commit()
    return {"results": res}
