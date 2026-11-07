"""Map extras on top of the device/gateway map: open case per device, coverage radius, homes without a button,
patients without a pin (+ pinning them); none of the home data goes to the read-only role."""
from app import codec
from app.db import SessionLocal
from app.models import Gateway, Patient
from tests.test_all import login, up

EUI = "70b3d57ed0000001"


def test_map_extras_and_pin(env_):
    eng, downs, notif, client, ids = env_
    H, V = login(client), login(client, "viewer")
    with SessionLocal() as db:
        db.add(Gateway(gateway_id="0016c001f1500001", label="รพ.สต.", lat=13.91, lon=100.60))
        db.add(Patient(full_name="นายบ้านไม่มีปุ่ม", village="ม.5", lat=13.92, lon=100.62)); db.commit()
    m = client.get("/api/map", headers=H).json()
    assert m["coverage_m"] == 3000 and m["gateways"][0]["lat"] == 13.91
    assert [p["name"] for p in m["no_location"]] == ["นางสมศรี ใจดี"]
    assert [p["name"] for p in m["homes"]] == ["นายบ้านไม่มีปุ่ม"]
    pid = ids["p"]
    assert client.put(f"/api/patients/{pid}/location", json={"lat": 95, "lon": 100}, headers=H).status_code == 422
    assert client.put(f"/api/patients/{pid}/location", json={"lat": 13.91, "lon": 100.61}, headers=V).status_code == 403
    assert client.put(f"/api/patients/{pid}/location", json={"lat": 13.9123456789, "lon": 100.61}, headers=H).json()["lat"] == 13.912346
    with SessionLocal() as db:
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=3)) == "created"
    m = client.get("/api/map", headers=H).json()
    d = next(x for x in m["devices"] if x["dev_eui"] == EUI)
    assert d["case"]["state"] == "new" and d["phone"] == "0800000000" and d["lat"] == 13.912346 and m["no_location"] == []
    v = client.get("/api/map", headers=V).json()
    assert v["restricted"] and v["homes"] == [] and v["no_location"] == []
    dv = next(x for x in v["devices"] if x["dev_eui"] == EUI)
    assert dv["lat"] is None and dv["phone"] == "" and dv["address"] == ""
    g = client.get("/api/settings", headers=H).json()["geo"]
    assert client.put("/api/settings/geo", json={**g, "coverage_m": 50}, headers=H).status_code == 422
    assert client.put("/api/settings/geo", json={**g, "coverage_m": 5000}, headers=H).status_code == 200
    assert client.get("/api/map", headers=H).json()["coverage_m"] == 5000
