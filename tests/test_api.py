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
              "/api/supply/setpoint", "/api/supply/pid", "/api/events", "/api/history.csv",
              "/api/output-limits", "/api/supply/output-limits", "/api/info",
              "/api/profiles/{name}/setpoint", "/api/profiles/{name}/overrides", "/api/supply/overrides"]:
        assert p in paths


def test_serves_front_end_without_token(env):
    client, _, _ = env
    page = client.get("/")
    assert page.status_code == 200
    assert "Still Control" in page.text
    for asset in ("/app.js", "/styles.css", "/fonts/fonts.css"):
        assert client.get(asset).status_code == 200, asset
    # only /api is protected, and the static mount does not shadow it
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers=AUTH).status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_output_limits_endpoints(env):
    client, controller, _ = env
    body = client.get("/api/state", headers=AUTH).json()
    assert body["output_limits"] == [30, 100]
    assert body["supply_output_limits"] == [0, 60]

    r = client.put("/api/output-limits", json={"min": 40, "max": 90}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["output_limits"] == [40, 90]
    r = client.put("/api/supply/output-limits", json={"min": 5, "max": 50}, headers=AUTH)
    assert r.json()["supply_output_limits"] == [5, 50]


@pytest.mark.parametrize("body", [
    {"min": 50, "max": 50}, {"min": 60, "max": 40}, {"min": -1, "max": 50}, {"min": 0, "max": 101}, {"min": 10},
])
def test_output_limits_validation(env, body):
    client, controller, _ = env
    for path in ("/api/output-limits", "/api/supply/output-limits"):
        assert client.put(path, json=body, headers=AUTH).status_code == 422
    assert client.put("/api/output-limits", json={"min": 1, "max": 2}).status_code == 401


def test_manual_mode_holds_positions_over_api(env):
    client, controller, _ = env
    for _ in range(3):
        auto = controller.tick()
    client.put("/api/mode", json={"mode": "manual"}, headers=AUTH)
    state = controller.tick()
    assert state.valves_pct == auto.valves_pct


def _log(loop, n=60, t0=1000.0, fault_at=None):
    from still.controller import ControllerState, Mode
    from still.hardware.base import Temperatures

    for i in range(n):
        fault = "boom" if fault_at is not None and fault_at <= i < fault_at + 3 else None
        loop._store.add_history(t0 + i, ControllerState(
            mode=Mode.AUTO, profile="whiskey", setpoint_f=130.0, temps_f=Temperatures(90, 120, 90, 100),
            supply_setpoint_f=90.0, fault=fault))


def test_history_until_and_step(env):
    client, _, loop = env
    _log(loop)
    r = client.get("/api/history", headers=AUTH, params={"until": 1009, "step": 5}).json()
    assert [p["timestamp"] for p in r] == [1000.0, 1005.0]
    r = client.get("/api/history", headers=AUTH, params={"since": 1049, "step": 5}).json()
    assert [p["timestamp"] for p in r] == [1050.0, 1055.0]
    assert client.get("/api/history", headers=AUTH, params={"step": 0}).status_code == 422


def test_events_endpoint(env):
    client, _, loop = env
    _log(loop, fault_at=20)
    body = client.get("/api/events", headers=AUTH).json()
    assert [e["text"] for e in body["events"] if e["kind"] == "fault"] == ["boom", "Fault cleared after 3 s"]
    assert body["events"][0]["kind"] == "log"
    assert [s["kind"] for s in body["segments"]] == ["auto", "fault", "auto"]
    assert client.get("/api/events", params={"since": 1000}).status_code == 401


def test_history_csv(env):
    client, _, loop = env
    _log(loop, n=10)
    assert client.get("/api/history.csv").status_code == 401
    r = client.get("/api/history.csv", headers=AUTH, params={"since": 1002, "until": 1004})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().split("\r\n")
    assert lines[0].startswith("timestamp,time_utc,mode,profile")
    assert len(lines) == 4
    first = lines[1].split(",")
    assert first[0] == "1002.000" and first[1] == "1970-01-01T00:16:42.000Z" and first[2] == "auto"


def test_profiles_report_defaults_and_saved_values(env):
    client, controller, _ = env
    body = client.get("/api/profiles", headers=AUTH).json()
    whiskey = next(p for p in body["profiles"] if p["name"] == "whiskey")
    assert whiskey["setpoint_f"] == whiskey["saved"]["setpoint_f"] == 130
    assert body["supply"]["setpoint_f"] == body["supply"]["saved"]["setpoint_f"] == 90
    assert body["supply"]["output_limits"] == [0, 60]

    client.put("/api/setpoint", json={"setpoint_f": 128}, headers=AUTH)
    client.put("/api/supply/setpoint", json={"setpoint_f": 88}, headers=AUTH)
    body = client.get("/api/profiles", headers=AUTH).json()
    whiskey = next(p for p in body["profiles"] if p["name"] == "whiskey")
    assert whiskey["setpoint_f"] == 130 and whiskey["saved"]["setpoint_f"] == 128  # default unchanged
    assert body["supply"]["setpoint_f"] == 90 and body["supply"]["saved"]["setpoint_f"] == 88


def test_set_setpoint_of_any_profile(env):
    client, controller, _ = env
    r = client.put("/api/profiles/gin/setpoint", json={"setpoint_f": 117}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["profile"] == "whiskey" and r.json()["setpoint_f"] == 130  # active one untouched
    r = client.put("/api/profiles/whiskey/setpoint", json={"setpoint_f": 126}, headers=AUTH)
    assert r.json()["setpoint_f"] == 126  # active: applies now
    gin = next(p for p in client.get("/api/profiles", headers=AUTH).json()["profiles"] if p["name"] == "gin")
    assert gin["saved"]["setpoint_f"] == 117
    assert client.put("/api/profiles/nope/setpoint", json={"setpoint_f": 100}, headers=AUTH).status_code == 404
    assert client.put("/api/profiles/gin/setpoint", json={"setpoint_f": 999}, headers=AUTH).status_code == 422
    assert client.put("/api/profiles/gin/setpoint", json={"setpoint_f": 100}).status_code == 401


def test_reset_overrides(env):
    client, controller, _ = env
    client.put("/api/setpoint", json={"setpoint_f": 120}, headers=AUTH)
    client.put("/api/output-limits", json={"min": 50, "max": 60}, headers=AUTH)
    client.put("/api/profiles/gin/setpoint", json={"setpoint_f": 111}, headers=AUTH)
    client.put("/api/supply/setpoint", json={"setpoint_f": 70}, headers=AUTH)

    r = client.delete("/api/profiles/whiskey/overrides", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["setpoint_f"] == 130 and r.json()["output_limits"] == [30, 100]
    gin = next(p for p in client.get("/api/profiles", headers=AUTH).json()["profiles"] if p["name"] == "gin")
    assert gin["saved"]["setpoint_f"] == 111  # untouched

    r = client.delete("/api/supply/overrides", headers=AUTH)
    assert r.json()["supply_setpoint_f"] == 90
    assert client.delete("/api/profiles/nope/overrides", headers=AUTH).status_code == 404
    assert client.delete("/api/supply/overrides").status_code == 401


def test_info(env):
    client, controller, loop = env
    body = client.get("/api/info", headers=AUTH).json()
    assert body["hardware"] == "simulated"
    assert body["version"] == client.get("/openapi.json").json()["info"]["version"]
    assert body["interval_s"] == 0.01
    assert body["retention_days"] == 14
    assert body["default_profile"] == "whiskey"
    assert body["startup_mode"] == "auto"
    assert body["thermistor"]["beta"] == 3380
    assert body["channels"]["ao"] == {"dephlegmator": 0, "condenser": 1, "supply": 2}
    assert client.get("/api/info").status_code == 401


@pytest.fixture
def sim_env():
    config = load_config(CONFIG_PATH)
    hw = SimulatedHW(seed=1)
    controller = Controller(hw, config)
    loop = ControlLoop(controller, interval_s=0.01)
    client = TestClient(create_app(controller, loop, config, TOKEN, simulator=hw))
    return client, controller


def test_sim_fault_routes_absent_without_simulator(env):
    client, _, _ = env
    assert client.get("/api/sim/fault", headers=AUTH).status_code == 404


def test_sim_fault_trips_failsafe_and_clears(sim_env):
    client, controller = sim_env
    controller.tick()
    assert client.get("/api/sim/fault", headers=AUTH).json() == {"sensor": None, "kind": None}

    r = client.put("/api/sim/fault", json={"sensor": "deph_return", "kind": "open"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"sensor": "deph_return", "kind": "open"}
    controller.tick()
    state = client.get("/api/state", headers=AUTH).json()
    assert state["fault"] is not None
    assert state["valves_pct"]["dephlegmator"] == 100

    assert client.delete("/api/sim/fault", headers=AUTH).json() == {"sensor": None, "kind": None}
    controller.tick()
    assert client.get("/api/state", headers=AUTH).json()["fault"] is None


def test_sim_fault_rejects_bad_input_and_needs_token(sim_env):
    client, _ = sim_env
    assert client.put("/api/sim/fault", json={"sensor": "x", "kind": "open"}, headers=AUTH).status_code == 422
    assert client.put("/api/sim/fault", json={"sensor": "deph_return"}, headers=AUTH).status_code == 422
    assert client.put("/api/sim/fault", json={"sensor": "deph_return", "kind": "open"}).status_code == 401
