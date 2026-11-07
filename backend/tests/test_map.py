"""Map data: patients with/without a pin, open cases, gateways + coverage; viewers get no home locations."""
from app import codec
from app.db import SessionLocal
from app.models import Gateway, Patient
from tests.test_all import login, up

EUI = "70b3d57ed0000001"


def test_map_and_pin(env_):
    eng, downs, notif, client, ids = env_
    H, V = login(client), login(client, "viewer")
    with SessionLocal() as db:
        db.add(Gateway(gateway_id="0016c001f1500001", label="รพ.สต.", lat=13.91, lon=100.60)); db.commit()
    m = client.get("/api/map", headers=H).json()
    assert m["coverage_m"] == 3000 and m["gateways"][0]["lat"] == 13.91 and m["patients"] == []
    assert [p["name"] for p in m["no_location"]] == ["นางสมศรี ใจดี"]
    pid = ids["p"]
    assert client.put(f"/api/patients/{pid}/location", json={"lat": 95, "lon": 100}, headers=H).status_code == 422
    assert client.put(f"/api/patients/{pid}/location", json={"lat": 13.9123456789, "lon": 100.61}, headers=V).status_code == 403
    r = client.put(f"/api/patients/{pid}/location", json={"lat": 13.9123456789, "lon": 100.61}, headers=H).json()
    assert r["lat"] == 13.912346
    with SessionLocal() as db:
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=3)) == "created"
    m = client.get("/api/map", headers=H).json()
    p = m["patients"][0]
    assert p["lat"] == 13.912346 and p["case"]["state"] == "new" and p["device"]["label"] == "A1" and p["device"]["online"]
    v = client.get("/api/map", headers=V).json()
    assert v["viewer"] and v["patients"] == [] and v["no_location"] == [] and len(v["gateways"]) == 1
    # coverage radius is a setting
    g = client.get("/api/settings", headers=H).json()["geo"]
    assert client.put("/api/settings/geo", json={**g, "coverage_m": 50}, headers=H).status_code == 422
    assert client.put("/api/settings/geo", json={**g, "coverage_m": 5000}, headers=H).status_code == 200
    assert client.get("/api/map", headers=H).json()["coverage_m"] == 5000
