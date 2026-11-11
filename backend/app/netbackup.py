"""Password-encrypted ChirpStack backup envelope (AES-GCM + scrypt)."""
from __future__ import annotations

import base64
import json
import secrets
import re
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

FORMAT = "sos-chirpstack-backup"
AAD = b"sos-chirpstack-backup:v1"
MAX_PLAIN = 12 * 1024 * 1024


def _key(password: str, salt: bytes) -> bytes:
    if not isinstance(password, str) or len(password) < 16 or len(password) > 128:
        raise ValueError("รหัสผ่านไฟล์สำรองต้องยาว 16–128 ตัวอักษร")
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(password.encode("utf-8"))


def encrypt(payload: dict, password: str) -> dict:
    clear = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(clear) > MAX_PLAIN:
        raise ValueError("ข้อมูลสำรองมีขนาดใหญ่เกินไป")
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    cipher = AESGCM(_key(password, salt)).encrypt(nonce, clear, AAD)
    enc = lambda b: base64.b64encode(b).decode("ascii")
    return {"format": FORMAT, "version": 1, "kdf": "scrypt-n16384-r8-p1", "cipher": "AES-256-GCM",
            "salt": enc(salt), "nonce": enc(nonce), "ciphertext": enc(cipher)}


def decrypt(envelope: dict, password: str) -> dict:
    if not isinstance(envelope, dict) or envelope.get("format") != FORMAT or envelope.get("version") != 1:
        raise ValueError("รูปแบบไฟล์สำรอง SOS ไม่ถูกต้อง")
    if envelope.get("kdf") != "scrypt-n16384-r8-p1" or envelope.get("cipher") != "AES-256-GCM":
        raise ValueError("อัลกอริทึมในไฟล์สำรองไม่รองรับ")
    try:
        dec = lambda k: base64.b64decode(envelope[k], validate=True)
        salt, nonce, cipher = dec("salt"), dec("nonce"), dec("ciphertext")
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError("ไฟล์สำรองเสียหาย") from e
    if len(salt) != 16 or len(nonce) != 12 or len(cipher) > MAX_PLAIN + 16:
        raise ValueError("ขนาดไฟล์สำรองไม่ถูกต้อง")
    try:
        clear = AESGCM(_key(password, salt)).decrypt(nonce, cipher, AAD)
    except InvalidTag as e:
        raise ValueError("รหัสผ่านไม่ถูกต้อง หรือไฟล์สำรองถูกแก้ไข") from e
    if len(clear) > MAX_PLAIN:
        raise ValueError("ข้อมูลสำรองมีขนาดใหญ่เกินไป")
    try:
        payload = json.loads(clear)
    except (UnicodeDecodeError, ValueError) as e:
        raise ValueError("ข้อมูลในไฟล์สำรองอ่านไม่ได้") from e
    if not isinstance(payload, dict) or payload.get("format") != FORMAT or payload.get("version") != 1:
        raise ValueError("ข้อมูลสำรองไม่ใช่ SOS ChirpStack backup")
    return payload


def validate_payload(payload: dict) -> dict:
    """Strict allow-list before any decrypted values can reach ChirpStack's write API."""
    if not isinstance(payload, dict) or payload.get("format") != FORMAT or payload.get("version") != 1:
        raise ValueError("เวอร์ชันข้อมูลสำรองไม่รองรับ")
    devices, gateways = payload.get("devices"), payload.get("gateways")
    if not isinstance(devices, list) or not isinstance(gateways, list) or len(devices) > 5000 or len(gateways) > 1000:
        raise ValueError("จำนวนอุปกรณ์หรือเกตเวย์ในไฟล์สำรองไม่ถูกต้อง")
    hex16 = re.compile(r"^[0-9a-fA-F]{16}$")
    hexkey = re.compile(r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{64})$")
    key_names = {"nwkKey", "appKey", "nwkSEncKey", "sNwkSIntKey", "fNwkSIntKey"}
    out_d, seen_d = [], set()
    for row in devices:
        if not isinstance(row, dict) or not hex16.fullmatch(str(row.get("dev_eui", ""))):
            raise ValueError("DevEUI ในไฟล์สำรองไม่ถูกต้อง")
        eui = row["dev_eui"].lower()
        if eui in seen_d: raise ValueError("พบ DevEUI ซ้ำในไฟล์สำรอง")
        seen_d.add(eui)
        keys = row.get("keys")
        if not isinstance(keys, dict) or not {"appKey", "nwkKey"}.issubset(keys) or any(k not in key_names or not hexkey.fullmatch(str(v)) for k, v in keys.items()):
            raise ValueError(f"Key ของ {eui} ไม่ครบหรือรูปแบบไม่ถูกต้อง")
        if any(k in keys and not hexkey.fullmatch(str(keys[k])) for k in ("nwkKey", "appKey")):
            raise ValueError(f"Key ของ {eui} ไม่ถูกต้อง")
        if not isinstance(row.get("is_disabled", False), bool) or not isinstance(row.get("skip_fcnt_check", False), bool):
            raise ValueError(f"สถานะอุปกรณ์ {eui} ไม่ถูกต้อง")
        out_d.append({"dev_eui": eui, "name": str(row.get("name", eui))[:100],
                      "description": str(row.get("description", ""))[:1024],
                      "is_disabled": row.get("is_disabled", False), "skip_fcnt_check": row.get("skip_fcnt_check", False),
                      "keys": {k: str(v).upper() for k, v in keys.items()}})
    out_g, seen_g = [], set()
    for row in gateways:
        if not isinstance(row, dict) or not hex16.fullmatch(str(row.get("gateway_id", ""))):
            raise ValueError("Gateway ID ในไฟล์สำรองไม่ถูกต้อง")
        gid = row["gateway_id"].lower()
        if gid in seen_g: raise ValueError("พบ Gateway ID ซ้ำในไฟล์สำรอง")
        seen_g.add(gid)
        loc = row.get("location") or {}
        if not isinstance(loc, dict): raise ValueError(f"ตำแหน่งเกตเวย์ {gid} ไม่ถูกต้อง")
        clean_loc = {}
        for key, lo, hi in (("latitude", -90, 90), ("longitude", -180, 180), ("altitude", -500, 10000)):
            val = loc.get(key)
            if val is not None:
                if isinstance(val, bool) or not isinstance(val, (int, float)) or not lo <= val <= hi:
                    raise ValueError(f"ตำแหน่งเกตเวย์ {gid} ไม่ถูกต้อง")
                clean_loc[key] = val
        tags = row.get("tags") or {}
        if not isinstance(tags, dict) or len(tags) > 50 or any(not isinstance(k, str) or not isinstance(v, str) for k, v in tags.items()):
            raise ValueError(f"Tags ของเกตเวย์ {gid} ไม่ถูกต้อง")
        si = row.get("stats_interval", 30)
        if isinstance(si, bool) or not isinstance(si, int) or not 1 <= si <= 3600:
            raise ValueError(f"ช่วงรายงานเกตเวย์ {gid} ไม่ถูกต้อง")
        out_g.append({"gateway_id": gid, "name": str(row.get("name", gid))[:100],
                      "description": str(row.get("description", ""))[:1024], "location": clean_loc,
                      "stats_interval": si, "tags": {str(k)[:100]: str(v)[:200] for k, v in tags.items()}})
    return {"format": FORMAT, "version": 1, "devices": out_d, "gateways": out_g}
