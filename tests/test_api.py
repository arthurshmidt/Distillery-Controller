import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from still.api import create_app
from still.config import load_config
from still.controller import Controller
from still.hardware.simulated import SimulatedHW
from still.runner import ControlLoop

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "still.yaml"
TOKEN = "s3cret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def env():
    config = load_config(CONFIG_PATH)
    controller = Controller(SimulatedHW(seed=1), config)
    loop = ControlLoop(controller, interval_s=0.01)
    client = TestClient(create_app(controller, loop, config, TOKEN))
    return client, controller, loop


def test_requires_token(env):
    client, _, _ = env
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/state", headers=AUTH).status_code == 200
    assert client.get("/api/state", params={"token": TOKEN}).status_code == 200
    assert client.put("/api/mode", json={"mode": "off"}).status_code == 401


def test_state_shape(env):
    client, controller, _ = env
    controller.tick()
    body = client.get("/api/state", headers=AUTH).json()
    assert body["mode"] == "auto"
    assert body["profile"] == "whiskey"
    assert set(body["temps_f"]) == {"deph_supply", "deph_return", "cond_supply", "cond_return"}
    assert set(body["valves_pct"]) == {"dephlegmator", "condenser", "supply"}
    assert body["supply_setpoint_f"] == 90
    assert body["supply_pid_gains"] == {"p": -1.0, "i": -0.01, "d": 0.0}
    assert body["pid_gains"] == {"p": -1.0, "i": -0.01, "d": 0.0}


def test_supply_setpoint_and_gains(env):
    client, controller, _ = env
    r = client.put("/api/supply/setpoint", json={"setpoint_f": 85}, headers=AUTH)
    assert r.json()["supply_setpoint_f"] == 85
    r = client.put("/api/supply/pid", json={"p": -2.0, "i": -0.02, "d": 0.1}, headers=AUTH)
    assert r.json()["supply_pid_gains"] == {"p": -2.0, "i": -0.02, "d": 0.1}
    assert controller.state().supply_pid_gains == (-2.0, -0.02, 0.1)
    assert client.put("/api/supply/setpoint", json={"setpoint_f": 999}, headers=AUTH).status_code == 422


def test_profiles_and_switch(env):
    client, _, _ = env
    body = client.get("/api/profiles", headers=AUTH).json()
    assert body["active"] == "whiskey"
    assert {p["name"] for p in body["profiles"]} == {"whiskey", "gin"}
    r = client.put("/api/profile", json={"name": "gin"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["profile"] == "gin" and r.json()["setpoint_f"] == 120
    assert client.put("/api/profile", json={"name": "rum"}, headers=AUTH).status_code == 404


def test_setpoint_and_validation(env):
    client, _, _ = env
    assert client.put("/api/setpoint", json={"setpoint_f": 140}, headers=AUTH).json()["setpoint_f"] == 140
    assert client.put("/api/setpoint", json={"setpoint_f": 9999}, headers=AUTH).status_code == 422


def test_mode_and_manual_valve(env):
    client, controller, _ = env
    # manual valve rejected outside manual mode
    r = client.put("/api/valves/dephlegmator", json={"percent": 40}, headers=AUTH)
    assert r.status_code == 409
    client.put("/api/mode", json={"mode": "manual"}, headers=AUTH)
    assert client.put("/api/valves/dephlegmator", json={"percent": 40}, headers=AUTH).status_code == 200
    assert controller.tick().valves_pct["dephlegmator"] == 40
    assert client.put("/api/valves/bogus", json={"percent": 40}, headers=AUTH).status_code == 404
    assert client.put("/api/valves/condenser", json={"percent": 101}, headers=AUTH).status_code == 422
    assert client.put("/api/mode", json={"mode": "nope"}, headers=AUTH).status_code == 422


def test_pid_gains(env):
    client, controller, _ = env
    r = client.put("/api/pid", json={"p": -2.0, "i": -0.02, "d": 0.1}, headers=AUTH)
    assert r.json()["pid_gains"] == {"p": -2.0, "i": -0.02, "d": 0.1}
    assert controller.state().pid_gains == (-2.0, -0.02, 0.1)


def test_set_profile_resets_gains_in_state(env):
    client, _, _ = env
    client.put("/api/pid", json={"p": -2.0, "i": 0.0, "d": 0.0}, headers=AUTH)
    r = client.put("/api/profile", json={"name": "gin"}, headers=AUTH)
    assert r.json()["pid_gains"]["p"] == -1.0


def test_history(env):
    client, _, loop = env
    loop.start()
    try:
        deadline = time.time() + 2
        while len(loop.history()) < 3 and time.time() < deadline:
            time.sleep(0.01)
    finally:
        loop.stop()
    body = client.get("/api/history", headers=AUTH, params={"limit": 2}).json()
    assert len(body) == 2
    assert body[0]["timestamp"] <= body[1]["timestamp"]
    assert "temps_f" in body[0]["state"]
    newest = loop.history()[-1].timestamp
    later = client.get("/api/history", headers=AUTH, params={"since": newest}).json()
    assert all(p["timestamp"] > newest for p in later)


def test_loop_stop_leaves_failsafe(env):
    _, controller, loop = env
    loop.start()
    loop.stop()
    s = controller.state()
    assert s.valves_pct["dephlegmator"] == 100.0 and s.valves_pct["condenser"] == 100.0
    assert s.fault is None


def test_stream_requires_token(env):
    client, _, _ = env
    assert client.get("/api/stream").status_code == 401


def test_openapi_has_all_routes(env):
    client, _, _ = env
    paths = client.get("/openapi.json").json()["paths"]
    for p in ["/api/state", "/api/stream", "/api/history", "/api/profiles", "/api/profile",
              "/api/setpoint", "/api/mode", "/api/valves/{name}", "/api/pid",
              "/api/supply/setpoint", "/api/supply/pid"]:
        assert p in paths
