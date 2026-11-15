from concurrent.futures import ThreadPoolExecutor
import json

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.main import create_app
from backend.models import Telemetry

DEVICE = {"X-Device-Key": "test-device"}
DASHBOARD = {"X-Dashboard-Key": "test-dashboard"}


class Clock:
    now = 1_700_000_000.0

    def __call__(self):
        return self.now


def reading(**changes):
    body = {"schema_version": 1, "device_id": "clip-1", "boot_id": "boot-1", "sequence": 0,
            "uptime_ms": 5000, "source": "hardware", "firmware_version": "0.1.0",
            "temperature_c": 23.0, "humidity_pct": 45.0, "environment_sensor_ok": True,
            "pulse_raw": None, "pulse_bpm": None, "pulse_quality": "disabled", "wifi_rssi_dbm": -55}
    return body | changes


@pytest.fixture
def rig(tmp_path):
    clock = Clock()
    settings = Settings("test-device", "test-dashboard", tmp_path / "test.sqlite3")
    app = create_app(settings, clock)
    with TestClient(app) as client:
        yield client, clock, app, settings


def post(client, **changes):
    return client.post("/v1/telemetry", headers=DEVICE, json=reading(**changes))


def snapshot(client):
    response = client.get("/v1/snapshot", headers=DASHBOARD)
    assert response.status_code == 200, response.text
    return response.json()


def test_auth_separates_device_and_dashboard(rig):
    client, *_ = rig
    assert client.get("/health").status_code == 200
    assert client.get("/v1/snapshot").status_code == 401
    assert client.get("/v1/snapshot", headers={"X-Dashboard-Key": "test-device"}).status_code == 401
    assert client.post("/v1/telemetry", json=reading()).status_code == 401
    assert client.post("/v1/telemetry", headers={"X-Device-Key": "test-dashboard"}, json=reading()).status_code == 401


def test_receive_history_assignment_and_response_contract(rig):
    client, clock, app, _ = rig
    assert post(client).status_code == 200
    response = client.patch("/v1/devices/clip-1", headers=DASHBOARD,
                            json={"worker_name": "Worker A", "site": "Roof"})
    assert response.status_code == 200
    item = snapshot(client)["devices"][0]
    assert item["risk_state"] == "normal"
    assert item["source"] == "hardware"
    assert item["worker_name"] == "Worker A"
    assert item["latest"]["temperature_c"] == 23
    first = client.get("/v1/devices/clip-1/history?limit=1", headers=DASHBOARD).json()
    clock.now += 5
    assert post(client, sequence=1, uptime_ms=10000).status_code == 200
    second = client.get(f"/v1/devices/clip-1/history?after_id={first['next_after_id']}", headers=DASHBOARD).json()
    assert len(second["items"]) == 1
    assert second["items"][0]["sequence"] == 1
    assert "Snapshot" in app.openapi()["components"]["schemas"]


def test_heat_alert_acknowledgment_escalation_and_recovery(rig):
    client, clock, *_ = rig
    assert post(client, temperature_c=32.0, humidity_pct=55.0).json()["assessment"]["state"] == "warning"
    alert = snapshot(client)["alerts"][0]
    ack = client.post(f"/v1/alerts/{alert['id']}/ack", headers=DASHBOARD, json={"acknowledged_by": "Adam"})
    assert ack.json()["acknowledged_by"] == "Adam"
    clock.now += 5
    assert post(client, temperature_c=37.0, humidity_pct=65.0, sequence=1, uptime_ms=10000).status_code == 200
    escalated = snapshot(client)["alerts"][0]
    assert escalated["id"] == alert["id"]
    assert escalated["severity"] == "critical"
    assert escalated["acknowledged_at"] is None
    clock.now += 5
    assert post(client, sequence=2, uptime_ms=15000).status_code == 200
    assert snapshot(client)["alerts"] == []
    history = client.get("/v1/alerts?active_only=false", headers=DASHBOARD).json()["items"]
    assert history[0]["resolved_at"] is not None
    assert client.post(f"/v1/alerts/{alert['id']}/ack", headers=DASHBOARD,
                       json={"acknowledged_by": "Adam"}).status_code == 409


def test_sensor_error_does_not_clear_existing_heat_alert(rig):
    client, clock, *_ = rig
    post(client, temperature_c=37.0, humidity_pct=65.0)
    clock.now += 5
    assert post(client, sequence=1, uptime_ms=10000, temperature_c=None, humidity_pct=None,
                environment_sensor_ok=False).status_code == 200
    data = snapshot(client)
    assert data["devices"][0]["risk_state"] == "unknown"
    assert {a["kind"] for a in data["alerts"]} == {"heat", "environment_sensor"}
    assert data["devices"][0]["latest"]["temperature_c"] is None
    post(client, sequence=2, uptime_ms=15000)
    assert snapshot(client)["alerts"] == []


def test_offline_state_uses_unknown_and_recovers(rig):
    client, clock, *_ = rig
    post(client)
    clock.now += 20
    data = snapshot(client)
    assert data["devices"][0]["connection_state"] == "offline"
    assert data["devices"][0]["risk_state"] == "unknown"
    assert data["devices"][0]["latest"]["assessment"]["state"] == "normal"
    assert len(data["alerts"]) == 1
    snapshot(client)
    assert len(snapshot(client)["alerts"]) == 1
    post(client, sequence=1, uptime_ms=30000)
    assert snapshot(client)["devices"][0]["connection_state"] == "online"
    assert snapshot(client)["alerts"] == []


def test_duplicate_does_not_refresh_liveness_or_duplicate_history(rig):
    client, clock, *_ = rig
    first = post(client).json()
    clock.now += 21
    second = post(client).json()
    assert second["duplicate"] is True
    assert second["telemetry_id"] == first["telemetry_id"]
    assert second["received_at"] == first["received_at"]
    assert snapshot(client)["devices"][0]["connection_state"] == "offline"
    assert len(client.get("/v1/devices/clip-1/history", headers=DASHBOARD).json()["items"]) == 1
    assert post(client, temperature_c=40.0).status_code == 409


def test_out_of_order_and_retired_boot_cannot_overwrite_live_readings(rig):
    client, *_ = rig
    post(client, sequence=3, uptime_ms=15000)
    assert post(client, sequence=2, uptime_ms=10000).status_code == 409
    assert post(client, sequence=4, uptime_ms=10000).status_code == 409
    assert post(client, boot_id="boot-2", sequence=0, uptime_ms=5000).status_code == 200
    assert post(client, sequence=4, uptime_ms=20000).status_code == 409
    assert snapshot(client)["devices"][0]["latest"]["boot_id"] == "boot-2"


@pytest.mark.parametrize("changes", [
    {"temperature_c": None}, {"humidity_pct": 101.0}, {"temperature_c": 100.0},
    {"environment_sensor_ok": False}, {"environment_sensor_ok": "true"},
    {"sequence": -1}, {"sequence": True}, {"device_id": "../bad"},
    {"pulse_bpm": 90.0}, {"pulse_raw": 100}, {"not_in_contract": 1},
])
def test_invalid_sensor_payload_is_rejected(rig, changes):
    client, *_ = rig
    assert post(client, **changes).status_code == 422
    assert snapshot(client)["devices"] == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_input_produces_json_validation_error(rig, value):
    client, *_ = rig
    response = client.post("/v1/telemetry", headers=DEVICE | {"Content-Type": "application/json"},
                           content=json.dumps(reading(temperature_c=value)))
    assert response.status_code == 422, response.text


def test_pulse_is_labeled_experimental_and_never_changes_heat_state(rig):
    client, *_ = rig
    response = post(client, pulse_raw=2048, pulse_bpm=190.0, pulse_quality="experimental")
    assert response.status_code == 200
    assert response.json()["assessment"]["state"] == "normal"


def test_simulator_cannot_impersonate_existing_hardware_device(rig):
    client, *_ = rig
    post(client)
    assert post(client, source="simulator", boot_id="sim-boot").status_code == 409
    assert post(client, device_id="demo-01", source="simulator").status_code == 200
    assert snapshot(client)["devices"][1]["source"] == "simulator"


def test_simulator_can_be_disabled(tmp_path):
    settings = Settings("test-device", "test-dashboard", tmp_path / "test.sqlite3", allow_simulator=False)
    with TestClient(create_app(settings)) as client:
        assert post(client, source="simulator").status_code == 403


def test_cors_allows_only_configured_origins(rig):
    client, *_ = rig
    headers = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET",
               "Access-Control-Request-Headers": "x-dashboard-key"}
    good = client.options("/v1/snapshot", headers=headers)
    assert good.status_code == 200
    assert good.headers["access-control-allow-origin"] == headers["Origin"]
    assert client.options("/v1/snapshot", headers=headers | {"Origin": "https://untrusted.example"}).status_code == 400


def test_state_and_metadata_survive_server_restart(rig):
    client, clock, _, settings = rig
    post(client)
    client.patch("/v1/devices/clip-1", headers=DASHBOARD, json={"worker_name": "Worker A"})
    with TestClient(create_app(settings, clock)) as reopened:
        assert snapshot(reopened)["devices"][0]["worker_name"] == "Worker A"
        assert len(reopened.get("/v1/devices/clip-1/history", headers=DASHBOARD).json()["items"]) == 1


def test_concurrent_retries_commit_one_reading(rig):
    _, clock, app, _ = rig
    model = Telemetry(**reading())
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: app.state.store.ingest(model, clock()), range(12)))
    assert len({result["telemetry_id"] for result in results}) == 1
    assert sum(not result["duplicate"] for result in results) == 1


def test_missing_resources_and_cursor_bounds(rig):
    client, *_ = rig
    assert client.get("/v1/devices/missing/history", headers=DASHBOARD).status_code == 404
    assert client.patch("/v1/devices/missing", headers=DASHBOARD, json={"site": "Roof"}).status_code == 404
    assert client.post("/v1/alerts/999/ack", headers=DASHBOARD,
                       json={"acknowledged_by": "Adam"}).status_code == 404
    assert client.get("/v1/alerts?limit=100000", headers=DASHBOARD).status_code == 422
    assert client.get("/v1/alerts?after_id=-1", headers=DASHBOARD).status_code == 422
