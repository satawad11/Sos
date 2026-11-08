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

    def set_disabled(self, dev_eui: str, disabled: bool) -> bool:
        """Returns True if ChirpStack was changed."""
        dev = self._call("GET", f"/api/devices/{dev_eui}").get("device") or {}
        if bool(dev.get("isDisabled")) == disabled:
            return False
        dev["isDisabled"] = disabled
        self._call("PUT", f"/api/devices/{dev_eui}", json={"device": dev})
        return True

    def flush_dev_nonces(self, dev_eui: str) -> None:
        """Forget the OTAA DevNonces ChirpStack has seen for this device (it ignores a JoinRequest that reuses one).
        Needed once after a device lost its saved DevNonce counter (firmware <= 0.2 bug, NVS erase, re-flash)."""
        self._call("DELETE", f"/api/devices/{dev_eui}/dev-nonces")

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
