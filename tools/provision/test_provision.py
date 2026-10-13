import json, httpx, provision as p


class FakeSerial:
    def __init__(self): self.out = []; self.buf = []; self.eui = None
    def write(self, b):
        cmd = b.decode().strip(); self.out.append(cmd)
        if cmd == "PING": self.buf.append(b"PONG\n")
        elif cmd.startswith("PROV"):
            self.eui = cmd.split()[1]; self.buf.append(f"OK deveui={self.eui}\n".encode())
        elif cmd == "INFO": self.buf.append(f"INFO fw=0.1 provisioned=1 deveui={self.eui} cfg_ver=1\n".encode())
    def readline(self): return self.buf.pop(0) if self.buf else b""


def test_identity_unique_and_valid():
    a, b = p.gen_identity(), p.gen_identity()
    assert a != b and len(a[0]) == 16 and len(a[1]) == 32 and a[0].startswith("02")
    int(a[0], 16); int(a[1], 16)


def test_flash_ok(monkeypatch):
    monkeypatch.setattr(p.time, "sleep", lambda s: None)
    s = FakeSerial(); eui, key = p.gen_identity()
    info = p.flash_serial(s, eui, key)
    assert eui in info and s.out[1].startswith(f"PROV {eui} {key}")


def test_flash_detects_mismatch(monkeypatch):
    import pytest
    monkeypatch.setattr(p.time, "sleep", lambda s: None)
    s = FakeSerial()
    orig = s.write
    def bad(b):
        orig(b)
        if b.decode().startswith("PROV"): s.eui = "0000000000000000"
    s.write = bad
    with pytest.raises(RuntimeError):
        p.flash_serial(s, "02aabbccddeeff00", "0" * 32)


def test_chirpstack_calls():
    calls = []
    def h(req):
        calls.append((req.method, req.url.path, json.loads(req.content or b"{}")))
        return httpx.Response(200, json={})
    cs = p.ChirpStack("http://cs", "k", "app", "prof", httpx.Client(transport=httpx.MockTransport(h)))
    cs.register("02aabbccddeeff00", "11" * 16, "SOS-001")
    assert calls[0][1] == "/api/devices" and calls[0][2]["device"]["applicationId"] == "app"
    assert calls[1][1] == "/api/devices/02aabbccddeeff00/keys" and calls[1][2]["deviceKeys"]["nwkKey"] == "11" * 16


def test_dry_run(capsys):
    assert p.main(["--dry-run", "--label", "X"]) == 0
    assert "DevEUI" in capsys.readouterr().out
