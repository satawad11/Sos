"""Server side of the web provisioning page.

The browser never holds the ChirpStack API key. Flow:
  start  -> backend creates DevEUI+AppKey, registers the device in ChirpStack and in sos_devices (lifecycle=ready),
            returns the keys ONCE to the logged-in admin so the page can write them to the device over USB.
  confirm-> page reports the INFO line read back from the device (proof the keys were stored).
  abort  -> page failed to write the device: remove the registration again (only while the device was never seen).
The AppKey is not stored by the backend. It lives in ChirpStack and on the device only.
"""
from __future__ import annotations
import secrets
import httpx
from .config import env


class ProvisionError(Exception):
    def __init__(self, msg: str, status: int = 0):
        super().__init__(msg)
        self.status = status

    @property
    def not_found(self) -> bool:
        return self.status == 404

    @property
    def exists(self) -> bool:
        return self.status == 409


def gen_identity() -> tuple[str, str]:
    return "02" + secrets.token_hex(7), secrets.token_hex(16)     # 0x02 = locally administered EUI-64


class ChirpStackRest:
    def __init__(self, base: str | None = None, api_key: str | None = None, app_id: str | None = None,
                 profile_id: str | None = None, client: httpx.Client | None = None):
        self.base = (base or env.cs_rest_url).rstrip("/")
        self.key, self.app_id, self.profile_id = api_key or env.cs_api_key, app_id or env.app_id, profile_id or env.cs_profile_id
        self.http = client or httpx.Client(timeout=15)

    @property
    def configured(self) -> bool:
        return bool(self.key and self.app_id and self.profile_id)

    def _h(self):
        return {"Authorization": f"Bearer {self.key}"}

    def register(self, dev_eui: str, app_key: str, name: str, desc: str = "") -> None:
        try:
            r = self.http.post(f"{self.base}/api/devices", headers=self._h(), json={"device": {
                "devEui": dev_eui, "name": name, "description": desc, "applicationId": self.app_id,
                "deviceProfileId": self.profile_id, "isDisabled": False, "skipFcntCheck": False}})
            r.raise_for_status()
            r = self.http.post(f"{self.base}/api/devices/{dev_eui}/keys", headers=self._h(), json={"deviceKeys": {
                "devEui": dev_eui, "nwkKey": app_key, "appKey": app_key}})
            r.raise_for_status()
        except httpx.HTTPError as e:
            try: self.delete(dev_eui)
            except Exception: pass
            raise ProvisionError(f"ChirpStack: {e.__class__.__name__}") from e

    def delete(self, dev_eui: str) -> None:
        self.http.delete(f"{self.base}/api/devices/{dev_eui}", headers=self._h()).raise_for_status()

    # ---- management from the dashboard (ChirpStack page / gateway + device sync) ----
    def _call(self, method: str, path: str, **kw) -> dict:
        try:
            r = self.http.request(method, f"{self.base}{path}", headers=self._h(), **kw)
        except httpx.HTTPError as e:
            raise ProvisionError(f"ChirpStack: {e.__class__.__name__}") from e
        if r.status_code >= 400:
            try: msg = r.json().get("message") or r.text
            except ValueError: msg = r.text
            raise ProvisionError(f"ChirpStack {r.status_code}: {str(msg)[:200]}", r.status_code)
        try: return r.json() if r.content else {}
        except ValueError: return {}

    def _pages(self, path: str, params: dict, limit: int = 100, cap: int = 5000) -> list[dict]:
        out: list[dict] = []
        while len(out) < cap:
            res = self._call("GET", path, params={**params, "limit": limit, "offset": len(out)}).get("result") or []
            out += res
            if len(res) < limit: break
        return out

    def application(self) -> dict:
        return self._call("GET", f"/api/applications/{self.app_id}").get("application") or {}

    def tenant_id(self) -> str:
        if not getattr(self, "_tenant", None):
            self._tenant = self.application().get("tenantId", "")
        return self._tenant

    def status(self) -> dict:
        """Connectivity check for the dashboard. Never raises."""
        if not self.configured:
            return {"configured": False, "reachable": False, "error": "ยังไม่ได้ตั้งค่า CHIRPSTACK_API_KEY / APP_ID / PROFILE_ID"}
        try:
            app = self.application()
            prof = self._call("GET", f"/api/device-profiles/{self.profile_id}").get("deviceProfile") or {}
            return {"configured": True, "reachable": True, "error": "", "application": app.get("name", ""),
                    "tenant_id": app.get("tenantId", ""), "device_profile": prof.get("name", ""),
                    "region": prof.get("region", ""), "mac_version": prof.get("macVersion", "")}
        except ProvisionError as e:
            return {"configured": True, "reachable": False, "error": str(e)}

    def list_devices(self) -> list[dict]:
        return self._pages("/api/devices", {"applicationId": self.app_id})

    def export_network_backup(self) -> dict:
        """Collect this SOS application's devices and keys plus tenant gateways for encrypted export."""
        devices = []
        for row in self.list_devices():
            eui = str(row.get("devEui", "")).lower()
            if not eui: continue
            d = self._call("GET", f"/api/devices/{eui}").get("device") or {}
            k = self._call("GET", f"/api/devices/{eui}/keys").get("deviceKeys") or {}
            keys = {name: str(k[name]) for name in ("nwkKey", "appKey", "nwkSEncKey", "sNwkSIntKey", "fNwkSIntKey")
                    if k.get(name)}
            devices.append({"dev_eui": eui, "name": str(d.get("name", row.get("name", ""))),
                            "description": str(d.get("description", "")), "is_disabled": bool(d.get("isDisabled", False)),
                            "skip_fcnt_check": bool(d.get("skipFcntCheck", False)), "keys": keys})
        gateways = []
        for row in self.list_gateways():
            gid = str(row.get("gatewayId", "")).lower()
            if not gid: continue
            g = self._call("GET", f"/api/gateways/{gid}").get("gateway") or {}
            gateways.append({"gateway_id": gid, "name": str(g.get("name", row.get("name", ""))),
                             "description": str(g.get("description", "")), "location": g.get("location") or {},
                             "stats_interval": int(g.get("statsInterval") or 30), "tags": g.get("tags") or {}})
        return {"format": "sos-chirpstack-backup", "version": 1, "application_name": self.application().get("name", ""),
                "device_profile_name": (self._call("GET", f"/api/device-profiles/{self.profile_id}").get("deviceProfile") or {}).get("name", ""),
                "devices": devices, "gateways": gateways}

    def restore_network_backup(self, payload: dict) -> dict:
        """Restore into the currently configured application/profile and tenant; existing entities are updated."""
        app = self.application()
        tenant_id = str(app.get("tenantId", ""))
        devices_created = devices_updated = gateways_created = gateways_updated = 0
        failed_devices, failed_gateways = [], []
        for d in payload["devices"]:
            eui = d["dev_eui"]
            try:
                body = {"devEui": eui, "name": d["name"], "description": d["description"], "applicationId": self.app_id,
                        "deviceProfileId": self.profile_id, "isDisabled": d["is_disabled"], "skipFcntCheck": d["skip_fcnt_check"]}
                try:
                    self._call("GET", f"/api/devices/{eui}")
                    self._call("PUT", f"/api/devices/{eui}", json={"device": body}); devices_updated += 1
                except ProvisionError as e:
                    if not e.not_found: raise
                    self._call("POST", "/api/devices", json={"device": body}); devices_created += 1
                try:
                    self._call("GET", f"/api/devices/{eui}/keys")
                    self._call("PUT", f"/api/devices/{eui}/keys", json={"deviceKeys": {"devEui": eui, **d["keys"]}})
                except ProvisionError as e:
                    if not e.not_found: raise
                    self._call("POST", f"/api/devices/{eui}/keys", json={"deviceKeys": {"devEui": eui, **d["keys"]}})
            except ProvisionError:
                failed_devices.append(eui)
        for g in payload["gateways"]:
            gid = g["gateway_id"]
            try:
                body = {"gatewayId": gid, "tenantId": tenant_id, "name": g["name"], "description": g["description"],
                        "location": g["location"], "statsInterval": g["stats_interval"], "tags": g["tags"]}
                try:
                    self._call("GET", f"/api/gateways/{gid}")
                    self._call("PUT", f"/api/gateways/{gid}", json={"gateway": body}); gateways_updated += 1
                except ProvisionError as e:
                    if not e.not_found: raise
                    self._call("POST", "/api/gateways", json={"gateway": body}); gateways_created += 1
            except ProvisionError:
                failed_gateways.append(gid)
        return {"devices_created": devices_created, "devices_updated": devices_updated,
                "gateways_created": gateways_created, "gateways_updated": gateways_updated,
                "failed_devices": failed_devices, "failed_gateways": failed_gateways}

    def profile_settings(self) -> dict:
        """Return only safe Device Profile controls exposed by the dashboard."""
        p = self._call("GET", f"/api/device-profiles/{self.profile_id}").get("deviceProfile") or {}
        algorithms = self._call("GET", "/api/device-profiles/adr-algorithms").get("result") or []
        return {"profile": p.get("name", ""), "adr_algorithm_id": p.get("adrAlgorithmId", ""),
                "adr_algorithms": [{"id": str(a.get("id", "")), "name": str(a.get("name", ""))}
                                   for a in algorithms if a.get("id")]}

    def update_profile_adr(self, algorithm_id: str) -> None:
        """Update only adrAlgorithmId; GET+PUT preserves every other profile field."""
        algorithms = self._call("GET", "/api/device-profiles/adr-algorithms").get("result") or []
        allowed = {str(a.get("id", "")) for a in algorithms}
        if algorithm_id not in allowed:
            raise ProvisionError("ADR algorithm นี้ไม่มีใน ChirpStack")
        p = self._call("GET", f"/api/device-profiles/{self.profile_id}").get("deviceProfile") or {}
        if not p:
            raise ProvisionError("ไม่พบ Device Profile ที่ตั้งค่าไว้", 404)
        p["adrAlgorithmId"] = algorithm_id
        self._call("PUT", f"/api/device-profiles/{self.profile_id}", json={"deviceProfile": p})

    def set_disabled(self, dev_eui: str, disabled: bool) -> bool:
        """Returns True if ChirpStack was changed."""
        dev = self._call("GET", f"/api/devices/{dev_eui}").get("device") or {}
        if bool(dev.get("isDisabled")) == disabled:
            return False
        dev["isDisabled"] = disabled
        self._call("PUT", f"/api/devices/{dev_eui}", json={"device": dev})
        return True

    def flush_queue(self, dev_eui: str) -> None:
        """Drop every downlink still waiting in ChirpStack for this device (stale ACK/MSG copies of old cases)."""
        self._call("DELETE", f"/api/devices/{dev_eui}/queue")

    def flush_dev_nonces(self, dev_eui: str) -> None:
        """Forget the OTAA DevNonces ChirpStack has seen for this device (it ignores a JoinRequest that reuses one).
        Needed once after a device lost its saved DevNonce counter (firmware <= 0.2 bug, NVS erase, re-flash)."""
        self._call("DELETE", f"/api/devices/{dev_eui}/dev-nonces")

    def enqueue(self, dev_eui: str, fport: int, data: bytes, confirmed: bool = False) -> str:
        """Queue a downlink through the REST API (returns ChirpStack's queue item id, used to match txack/ack events)."""
        import base64
        res = self._call("POST", f"/api/devices/{dev_eui}/queue", json={"queueItem": {
            "confirmed": confirmed, "fPort": fport, "data": base64.b64encode(data).decode()}})
        return str(res.get("id", ""))

    def gateway(self, gateway_id: str) -> dict:
        """{gateway: {...location...}, lastSeenAt, createdAt, ...}"""
        return self._call("GET", f"/api/gateways/{gateway_id}")

    def set_gateway_location(self, gateway_id: str, lat: float, lon: float, alt: float = 0.0) -> bool:
        """Copy a position (the gateway's own GPS fix) into ChirpStack. Returns True if it changed."""
        gw = self.gateway(gateway_id).get("gateway") or {}
        loc = gw.get("location") or {}
        if loc and abs((loc.get("latitude") or 0) - lat) < 1e-5 and abs((loc.get("longitude") or 0) - lon) < 1e-5:
            return False
        gw["location"] = {**loc, "latitude": lat, "longitude": lon, "altitude": alt or loc.get("altitude", 0), "source": "GPS"}
        self._call("PUT", f"/api/gateways/{gateway_id}", json={"gateway": gw})
        return True

    def device_known(self, dev_eui: str) -> bool | None:
        """True/False = ChirpStack has / does not have it; None = could not ask."""
        if not self.configured: return None
        try:
            self._call("GET", f"/api/devices/{dev_eui}"); return True
        except ProvisionError as e:
            return False if e.not_found else None

    def list_gateways(self) -> list[dict]:
        return self._pages("/api/gateways", {"tenantId": self.tenant_id()})

    def remove_device(self, dev_eui: str) -> bool:
        """Delete the device in ChirpStack. True if it existed, False if it was already gone."""
        try: self._call("DELETE", f"/api/devices/{dev_eui}")
        except ProvisionError as e:
            if e.not_found: return False
            raise
        return True

    def remove_gateway(self, gateway_id: str) -> bool:
        try: self._call("DELETE", f"/api/gateways/{gateway_id}")
        except ProvisionError as e:
            if e.not_found: return False
            raise
        return True

    def ensure_gateway(self, gateway_id: str, name: str, lat: float | None = None, lon: float | None = None) -> str:
        """Register the gateway in ChirpStack. Returns "created" or "exists"."""
        gw = {"gatewayId": gateway_id, "name": (name or gateway_id)[:100], "description": "added from SOS dashboard",
              "tenantId": self.tenant_id(), "statsInterval": 30, "tags": {"source": "sos-dashboard"}}
        if lat is not None and lon is not None:
            gw["location"] = {"latitude": lat, "longitude": lon, "altitude": 0}
        try:
            self._call("POST", "/api/gateways", json={"gateway": gw})
            return "created"
        except ProvisionError as e:
            if e.exists: return "exists"
            raise


_cs = {"client": None}


def get_cs() -> ChirpStackRest:
    return _cs["client"] or ChirpStackRest()
