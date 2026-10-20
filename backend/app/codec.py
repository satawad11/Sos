"""Payload codec — implements the central contract (v0.2).

Uplink (8 bytes): [ver<<4|type, alert_id, flags, battery, fw_major, fw_minor, config_ver, poll_count]
Uplink LOC (FPort 4, 9 bytes, optional GPS hardware): [ver<<4|8, alert_id, lat24(3, BE), lon24(3, BE), sats<<4|hdop2]
    lat24 = round((lat+90)/180*2^24)   lon24 = round((lon+180)/360*2^24)   (~1.2 m north-south, ~2.3 m east-west at the equator)
    sats = 0..15 (15 = 15 or more)     hdop2 = round(HDOP*2) 0..15 (so HDOP 0.5 steps, 15 = 7.5 or worse)
Downlink ACK_L2 (FPort 10, 3 bytes): [0x16, alert_id, result]
Downlink CONFIG (FPort 11, 6 bytes): [0x17, config_ver, hb_x5min, poll_s, buzzer, hold_x100ms]
"""
from __future__ import annotations
import base64, math
from dataclasses import dataclass

PROTO_VERSION = 1
T_ALERT, T_CANCEL, T_TEST, T_HEARTBEAT, T_POLL, T_ACK_L2, T_CONFIG, T_LOC = 1, 2, 3, 4, 5, 6, 7, 8
TYPE_NAMES = {1: "ALERT", 2: "CANCEL", 3: "TEST", 4: "HEARTBEAT", 5: "ALERT_POLL"}

F_MAINS_OK, F_BATT_LOW, F_CHARGING, F_TEST_MODE = 0x01, 0x02, 0x04, 0x08

R_ACCEPTED, R_CLOSED, R_FALSE_ALARM, R_AFTER_HOURS = 1, 2, 3, 4

FPORT_EVENT, FPORT_HEARTBEAT, FPORT_POLL, FPORT_LOC, FPORT_ACK_L2, FPORT_CONFIG = 1, 2, 3, 4, 10, 11


class CodecError(ValueError):
    pass


@dataclass
class Uplink:
    msg_type: int
    alert_id: int
    flags: int
    battery: int          # percent 0-100 (255 = unknown)
    fw_major: int
    fw_minor: int
    config_ver: int
    poll_count: int

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.msg_type, f"UNKNOWN({self.msg_type})")

    @property
    def mains_ok(self) -> bool: return bool(self.flags & F_MAINS_OK)
    @property
    def batt_low(self) -> bool: return bool(self.flags & F_BATT_LOW)
    @property
    def charging(self) -> bool: return bool(self.flags & F_CHARGING)
    @property
    def test_mode(self) -> bool: return bool(self.flags & F_TEST_MODE)
    @property
    def fw(self) -> str: return f"{self.fw_major}.{self.fw_minor}"


def decode_uplink(data: bytes) -> Uplink:
    if len(data) != 8:
        raise CodecError(f"uplink must be 8 bytes, got {len(data)}")
    ver, mtype = data[0] >> 4, data[0] & 0x0F
    if ver != PROTO_VERSION:
        raise CodecError(f"unsupported protocol version {ver}")
    if mtype not in TYPE_NAMES:
        raise CodecError(f"unknown message type {mtype}")
    return Uplink(mtype, data[1], data[2], data[3], data[4], data[5], data[6], data[7])


def encode_uplink(u: Uplink) -> bytes:
    return bytes([(PROTO_VERSION << 4) | u.msg_type, u.alert_id, u.flags, u.battery,
                  u.fw_major, u.fw_minor, u.config_ver, u.poll_count])


def encode_ack_l2(alert_id: int, result: int) -> bytes:
    if result not in (1, 2, 3, 4):
        raise CodecError("bad ACK_L2 result")
    return bytes([(PROTO_VERSION << 4) | T_ACK_L2, alert_id & 0xFF, result])


def encode_config(config_ver: int, hb_min: int, poll_s: int, buzzer: int, hold_ms: int) -> bytes:
    hb = max(1, min(255, round(hb_min / 5)))
    hold = max(1, min(255, round(hold_ms / 100)))
    return bytes([(PROTO_VERSION << 4) | T_CONFIG, config_ver & 0xFF, hb,
                  max(1, min(255, poll_s)), max(0, min(3, buzzer)), hold])


@dataclass
class Loc:
    alert_id: int
    lat: float
    lon: float
    sats: int
    hdop: float


_Q = 1 << 24


def encode_loc(alert_id: int, lat: float, lon: float, sats: int, hdop: float) -> bytes:
    # floor(x + 0.5), NOT round(): Python rounds half to even, C++ would round half up — the firmware must give identical bytes
    la = max(0, min(_Q - 1, math.floor((lat + 90) / 180 * _Q + 0.5)))
    lo = max(0, min(_Q - 1, math.floor((lon + 180) / 360 * _Q + 0.5)))
    q = (max(0, min(15, sats)) << 4) | max(0, min(15, math.floor(hdop * 2 + 0.5)))
    return bytes([(PROTO_VERSION << 4) | T_LOC, alert_id & 0xFF]) + la.to_bytes(3, "big") + lo.to_bytes(3, "big") + bytes([q])


def decode_loc(data: bytes) -> Loc:
    from .geo import valid_latlon
    if len(data) != 9:
        raise CodecError(f"LOC must be 9 bytes, got {len(data)}")
    if data[0] >> 4 != PROTO_VERSION or data[0] & 0x0F != T_LOC:
        raise CodecError("not a LOC message")
    lat = round(int.from_bytes(data[2:5], "big") / _Q * 180 - 90, 6)
    lon = round(int.from_bytes(data[5:8], "big") / _Q * 360 - 180, 6)
    sats, hd = data[8] >> 4, (data[8] & 0x0F) / 2
    if sats < 1:
        raise CodecError("LOC without satellites (no fix)")
    if not valid_latlon(lat, lon):
        raise CodecError("LOC coordinates invalid")
    return Loc(data[1], lat, lon, sats, hd)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def unb64(s: str) -> bytes:
    return base64.b64decode(s)
