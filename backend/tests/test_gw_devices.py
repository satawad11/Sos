"""Gateway list counts active devices and every device last heard via the gateway, so a button that is
still 'ready' (e.g. no PDPA consent yet) does not look as if the gateway never heard it."""
from app import codec
from app.db import SessionLocal
from app.models import Device
from tests.test_all import login, up

EUI = "70b3d57ed0000001"
GW = "2ccf67fffe9643aa"


def test_gateway_counts_ready_devices_as_heard(env_):
    eng, downs, notif, client, ids = env_
    H = login(client)
    with SessionLocal() as db:
        db.query(Device).filter_by(dev_eui=EUI).one().lifecycle = "ready"; db.commit()
        assert eng.handle_uplink(db, EUI.upper(), up(codec.T_HEARTBEAT), {"gateway_id": GW, "rssi": -80}) is not None
    g = next(x for x in client.get("/api/gateways", headers=H).json() if x["gateway_id"] == GW)
    assert g["devices"] == 0 and g["devices_heard"] == 1
    with SessionLocal() as db:
        db.query(Device).filter_by(dev_eui=EUI).one().lifecycle = "active"; db.commit()
    g = next(x for x in client.get("/api/gateways", headers=H).json() if x["gateway_id"] == GW)
    assert g["devices"] == 1 and g["devices_heard"] == 1
