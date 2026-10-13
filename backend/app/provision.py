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
    pass


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


_cs = {"client": None}


def get_cs() -> ChirpStackRest:
    return _cs["client"] or ChirpStackRest()
