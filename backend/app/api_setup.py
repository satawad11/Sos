"""Device provisioning (Web Serial), ChirpStack status/sync and firmware images for the USB updater.
Split out of api.py; shared helpers and the engine handle still live there."""
from __future__ import annotations
from fastapi import APIRouter
from . import provision as prov, updates as upd
from .auth import admin_only, audit, staff_or_admin
from .config import env
from .db import get_db
from .models import Device, Gateway
from fastapi import Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api")

# ---------------- provisioning (web page, Web Serial) ----------------
class ProvStart(BaseModel):
    label: str = Field(min_length=1, max_length=64)


class ProvConfirm(BaseModel):
    info: str = Field(max_length=400)


@router.get("/provision/status")
def prov_status(u=Depends(admin_only)):
    return {"configured": prov.get_cs().configured}


@router.post("/provision/start")
def prov_start(b: ProvStart, db: Session = Depends(get_db), u=Depends(admin_only)):
    cs = prov.get_cs()
    if not cs.configured: raise HTTPException(503, "ChirpStack provisioning is not configured (CS API key / profile id)")
    label = b.label.strip()
    if not label: raise HTTPException(422, "label required")
    if db.query(Device).filter_by(label=label).first(): raise HTTPException(409, "label already used")
    eui, key = prov.gen_identity()
    try:
        cs.register(eui, key, label, "provisioned by " + u.username)
    except prov.ProvisionError as e:
        raise HTTPException(502, str(e))
    try:
        db.add(Device(dev_eui=eui, label=label, lifecycle="ready")); audit(db, u.username, "provision_start", eui, label=label)
        db.commit()
    except Exception:
        db.rollback()
        try: cs.delete(eui)
        except Exception: pass
        raise HTTPException(500, "could not save device")
    return {"dev_eui": eui, "app_key": key, "join_eui": "0000000000000000", "label": label}


def _fresh_device(db, eui):
    d = db.query(Device).filter_by(dev_eui=eui.lower()).first()
    if not d: raise HTTPException(404)
    return d


@router.post("/provision/{eui}/confirm")
def prov_confirm(eui: str, b: ProvConfirm, db: Session = Depends(get_db), u=Depends(admin_only)):
    d = _fresh_device(db, eui)
    if f"deveui={d.dev_eui}" not in b.info.lower() or "provisioned=1" not in b.info:
        raise HTTPException(422, "device readback does not match")
    audit(db, u.username, "provision_confirmed", d.dev_eui, info=b.info[:200]); db.commit()
    return {"ok": True, "dev_eui": d.dev_eui, "label": d.label}


@router.post("/devices/{did}/flush-nonces")
def flush_nonces(did: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    """Device shows NO NETWORK after a reboot/re-flash because ChirpStack ignores reused DevNonces: clear them."""
    d = db.get(Device, did)
    if not d: raise HTTPException(404)
    cs = prov.get_cs()
    if not cs.configured: raise HTTPException(409, "ChirpStack API ยังไม่ได้ตั้งค่า")
    try: cs.flush_dev_nonces(d.dev_eui)
    except prov.ProvisionError as e: raise HTTPException(502, str(e))
    audit(db, u.username, "device_flush_nonces", d.dev_eui); db.commit()
    return {"ok": True}


@router.post("/provision/{eui}/abort")
def prov_abort(eui: str, db: Session = Depends(get_db), u=Depends(admin_only)):
    d = _fresh_device(db, eui)
    if d.lifecycle != "ready" or d.last_seen is not None or d.patient_id:
        raise HTTPException(409, "device already in use; retire it instead")
    try: prov.get_cs().delete(d.dev_eui)
    except Exception as e: audit(db, u.username, "provision_abort_cs_failed", d.dev_eui, err=str(e)[:100])
    audit(db, u.username, "provision_abort", d.dev_eui); db.delete(d); db.commit()
    return {"ok": True}


@router.get("/provision/{eui}/label.svg")
def prov_label(eui: str, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    """QR (DevEUI) as SVG for the sticker on the box/device. Contains no secrets."""
    import io
    import qrcode
    import qrcode.image.svg
    from fastapi.responses import Response
    d = _fresh_device(db, eui)
    img = qrcode.make(d.dev_eui, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO(); img.save(buf)
    return Response(buf.getvalue(), media_type="image/svg+xml")


# ---------------- ChirpStack (network server) — status, compare and sync from the dashboard ----------------
def _cs_compare(db: Session, cs) -> dict:
    cs_devs = {d["devEui"].lower(): d for d in cs.list_devices()}
    sos_devs = {d.dev_eui: d for d in db.query(Device).all()}
    cs_gws = {g["gatewayId"].lower() for g in cs.list_gateways()}
    sos_gws = db.query(Gateway).filter(Gateway.enabled.isnot(False)).all()
    return {
        "devices_cs": len(cs_devs), "devices_sos": len(sos_devs),
        # registered in SOS but ChirpStack does not know it: the button cannot join -> provision it again
        "missing_in_cs": [{"dev_eui": e, "label": d.label, "lifecycle": d.lifecycle}
                          for e, d in sos_devs.items() if e not in cs_devs and d.lifecycle != "retired"],
        # in ChirpStack but not in SOS: its alarms reach nobody
        "unknown_in_sos": [{"dev_eui": e, "name": d.get("name", ""), "last_seen": d.get("lastSeenAt")}
                           for e, d in cs_devs.items() if e not in sos_devs],
        "disabled_mismatch": [e for e, d in sos_devs.items()
                              if e in cs_devs and bool(cs_devs[e].get("isDisabled")) != (d.lifecycle == "retired")],
        "gateways_cs": len(cs_gws),
        "gateways_missing_in_cs": [{"gateway_id": g.gateway_id, "label": g.label} for g in sos_gws if g.gateway_id not in cs_gws],
    }


@router.get("/chirpstack")
def chirpstack_status(db: Session = Depends(get_db), u=Depends(admin_only)):
    cs = prov.get_cs()
    st = cs.status()
    st["ui_url"] = f"https://{env.cs_domain}" if env.cs_domain else ""
    if st.get("reachable"):
        try: st["compare"] = _cs_compare(db, cs)
        except Exception as e: st["compare_error"] = str(e)[:200]
    return st


@router.post("/chirpstack/sync")
def chirpstack_sync(db: Session = Depends(get_db), u=Depends(admin_only)):
    """Make ChirpStack match the dashboard: register missing gateways, disable retired buttons / enable the rest.
    Never creates or deletes devices (keys only exist on the button + ChirpStack; use the install page for that)."""
    cs = prov.get_cs()
    if not cs.configured: raise HTTPException(503, "ChirpStack is not configured (CS API key / app id / profile id)")
    try:
        cmp_ = _cs_compare(db, cs)
    except prov.ProvisionError as e:
        raise HTTPException(502, str(e))
    done = {"gateways_added": [], "devices_disabled": [], "devices_enabled": [], "errors": []}
    for g in cmp_["gateways_missing_in_cs"]:
        gw = db.query(Gateway).filter_by(gateway_id=g["gateway_id"]).one()
        try:
            if cs.ensure_gateway(gw.gateway_id, gw.label, gw.lat, gw.lon) == "created":
                done["gateways_added"].append(gw.gateway_id)
        except Exception as e:
            done["errors"].append(f"gateway {gw.gateway_id}: {str(e)[:120]}")
    for eui in cmp_["disabled_mismatch"]:
        d = db.query(Device).filter_by(dev_eui=eui).one()
        try:
            cs.set_disabled(eui, d.lifecycle == "retired")
            done["devices_disabled" if d.lifecycle == "retired" else "devices_enabled"].append(eui)
        except Exception as e:
            done["errors"].append(f"device {eui}: {str(e)[:120]}")
    audit(db, u.username, "chirpstack_sync", "", gateways=len(done["gateways_added"]),
          disabled=len(done["devices_disabled"]), enabled=len(done["devices_enabled"]), errors=len(done["errors"]))
    db.commit()
    return done

# ---------------- device firmware over USB (images from the firmware-bin branch; docs/FIRMWARE-UPDATE.md) ----------------
@router.get("/firmware")
def firmware_list(u=Depends(staff_or_admin)):
    return upd.firmware()


@router.get("/firmware/{env_name}.bin")
def firmware_file(env_name: str, u=Depends(staff_or_admin)):
    if env_name not in {b["env"] for b in upd.firmware()["builds"]}:
        raise HTTPException(404, "ไม่มีเฟิร์มแวร์สำหรับบอร์ดนี้")
    return FileResponse(upd.firmware_path(env_name), media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store"})


class FlashIn(BaseModel):
    dev_eui: str = Field(default="", pattern="^([0-9a-fA-F]{16})?$")
    env: str = Field(max_length=40)
    version: str = Field(max_length=16)
    from_version: str = Field(default="", max_length=16)
    ok: bool
    error: str = Field(default="", max_length=200)


@router.post("/firmware/flashed")
def firmware_flashed(b: FlashIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    """The browser flashed a button over USB and read INFO back — record it (the version also arrives with the next uplink)."""
    eui = b.dev_eui.lower()
    d = db.query(Device).filter_by(dev_eui=eui).first() if eui else None
    if d is not None and b.ok:
        d.fw_version = b.version
    audit(db, u.username, "firmware_flash" if b.ok else "firmware_flash_failed", eui or "-",
          env=b.env, version=b.version, previous=b.from_version, known_device=d is not None, **({"error": b.error} if b.error else {}))
    db.commit()
    return {"ok": True, "device_id": d.id if d else None}
