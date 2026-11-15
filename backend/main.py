from contextlib import asynccontextmanager
import asyncio
import hmac
import logging
import threading
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from .config import Settings
from .demo_scenarios import DemoScenario, make_demo_reading
from .models import (Alert, AlertAcknowledgment, AlertPage, DemoScenarioResult, DeviceList, DeviceUpdate,
                     HistoryPage, Receipt, Snapshot, Telemetry, TigerDataStatus)
from .prediction import build_prediction
from .risk import DEMO_SCORE_WEIGHTS, HEAT_INDEX_BANDS_C, MODEL_VERSION, RISK_SCORE_THRESHOLDS
from .store import Conflict, Missing, Store, iso
from .tigerdata import TigerDataSink
from .weather import WeatherClient

logger = logging.getLogger("thermosafe.tigerdata")


def create_app(settings: Settings | None = None, clock=time.time) -> FastAPI:
    settings = settings or Settings.from_env()
    store = Store(settings.db_path, settings.offline_seconds)
    tigerdata = TigerDataSink(settings.tigerdata_dsn)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()

        async def monitor():
            while True:
                await run_in_threadpool(store.mark_offline, clock())
                await run_in_threadpool(weather.get)
                await asyncio.sleep(2)

        async def upload_to_tigerdata():
            while True:
                if tigerdata.configured:
                    rows = []
                    try:
                        rows = await run_in_threadpool(store.pending_tiger_uploads, 100)
                        if rows:
                            ids = [row["id"] for row in rows]
                            await run_in_threadpool(tigerdata.upload_batch, rows)
                            await run_in_threadpool(store.mark_tiger_uploads_synced, ids, clock())
                    except Exception as error:
                        error_name = type(error).__name__
                        tigerdata.record_error(error)
                        if rows:
                            ids = [row["id"] for row in rows]
                            await run_in_threadpool(store.mark_tiger_uploads_failed,
                                                    ids, clock(), error_name)
                        logger.warning("TigerData sync failed; %d rows remain queued (%s)",
                                       len(rows), error_name)
                await asyncio.sleep(5)

        monitor_task = asyncio.create_task(monitor())
        tiger_task = asyncio.create_task(upload_to_tigerdata())
        if not tigerdata.configured:
            logger.warning("TigerData DSN is not configured; telemetry is queued in local SQLite only")
        try:
            yield
        finally:
            monitor_task.cancel()
            tiger_task.cancel()
            await asyncio.gather(monitor_task, tiger_task, return_exceptions=True)

    app = FastAPI(title="ThermoSafe API", version="0.1.0", lifespan=lifespan,
                  description="Prototype heat and cumulative exposure telemetry. Dashboard polling interval: 1 second.")
    app.state.store = store
    weather = WeatherClient(settings.weather_latitude, settings.weather_longitude,
                            settings.weather_location_name)
    app.state.weather = weather
    app.state.tigerdata = tigerdata
    app.state.alert_test_commands = {}
    app.state.alert_test_lock = threading.Lock()
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                       allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["X-Dashboard-Key", "Content-Type"])

    def device_auth(x_device_key: str = Header(default="")):
        if not hmac.compare_digest(x_device_key.encode(), settings.device_key.encode()):
            raise HTTPException(401, "Invalid device key")

    def dashboard_auth(x_dashboard_key: str = Header(default="")):
        if not hmac.compare_digest(x_dashboard_key.encode(), settings.dashboard_key.encode()):
            raise HTTPException(401, "Invalid dashboard key")

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(Missing)
    async def missing_handler(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        # Avoid echoing non-finite JSON values or raw request data in error output.
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()
        ]})

    @app.get("/health", tags=["health"])
    def health():
        return {"status": "ok", "version": "0.1.0", "server_time": iso(clock())}

    @app.get("/v1/tigerdata/status", response_model=TigerDataStatus,
             dependencies=[Depends(dashboard_auth)], tags=["dashboard"])
    def tigerdata_status():
        return {**tigerdata.status(), **store.tiger_outbox_status()}

    @app.post("/v1/telemetry", response_model=Receipt, dependencies=[Depends(device_auth)], tags=["devices"])
    def telemetry(reading: Telemetry):
        if reading.source == "simulator" and not settings.allow_simulator:
            raise HTTPException(403, "Simulator input is disabled")
        weather_data = weather.get_cached()
        current = weather_data.get("current") or {}
        if reading.demo_scenario_simulated and reading.site_uv_index_source == "simulated_demo":
            uv_index = reading.site_uv_index
        else:
            uv_index = current.get("uv_index") if weather_data.get("available") and not weather_data.get("stale") else None
        return store.ingest(reading, clock(), uv_index)

    @app.get("/v1/config", dependencies=[Depends(dashboard_auth)], tags=["dashboard"])
    def config():
        return {"schema_version": 1, "model_version": MODEL_VERSION,
                "heat_index_bands_c": HEAT_INDEX_BANDS_C,
                "demo_score_weights": DEMO_SCORE_WEIGHTS,
                "combined_risk_score_thresholds": RISK_SCORE_THRESHOLDS,
                "exposure_score_saturation_seconds": 1800,
                "offline_seconds": settings.offline_seconds, "poll_interval_ms": 1000,
                "expected_telemetry_interval_ms": 1000, "pulse_used_for_risk": False,
                "tigerdata_upload": {"enabled": tigerdata.configured,
                                     "table": "thermosafe_telemetry",
                                     "retry_queue": "local SQLite outbox"},
                "weather": {"provider": "Open-Meteo", "location": settings.weather_location_name,
                            "latitude": settings.weather_latitude, "longitude": settings.weather_longitude,
                            "uv_index_in_risk_assessment": True,
                            "uv_index_score_saturation": 11.0},
                "limitations": "Hackathon demonstration score, not medical or occupational guidance. Site UV is a weather-provider estimate, not a wearable measurement. Heat index does not account for direct solar radiation, exertion, clothing, or acclimatization. Photoresistor reports relative brightness, not UV or solar radiation."}

    @app.get("/v1/weather", dependencies=[Depends(dashboard_auth)], tags=["dashboard"])
    def site_weather():
        return weather.get()

    @app.get("/v1/devices", response_model=DeviceList, dependencies=[Depends(dashboard_auth)], tags=["dashboard"])
    def devices():
        now = clock()
        store.mark_offline(now)
        return {"items": store.devices(now), "server_time": iso(now)}

    @app.post("/v1/demo/scenarios/{scenario}", response_model=DemoScenarioResult,
              dependencies=[Depends(dashboard_auth)], tags=["dashboard"])
    def demo_scenario(scenario: DemoScenario):
        if not settings.allow_simulator:
            raise HTTPException(403, "Demo simulator input is disabled")
        device_id = f"demo-test-{scenario}"
        weather_data = weather.get_cached()
        current = weather_data.get("current") or {}
        uv_index = current.get("uv_index") if weather_data.get("available") and not weather_data.get("stale") else None
        reading = make_demo_reading(scenario, device_id, uv_index)
        receipt = store.ingest(reading, clock(), uv_index)
        return {"scenario": scenario, "device_id": device_id, "source": "simulator",
                "sample": reading.model_dump(), "telemetry_id": receipt["telemetry_id"],
                "received_at": receipt["received_at"], "assessment": receipt["assessment"]}

    @app.post("/v1/devices/{device_id}/alert-test/{state}", dependencies=[Depends(dashboard_auth)],
              tags=["dashboard"])
    def set_device_alert_test(device_id: str, state: DemoScenario):
        device = next((item for item in store.devices(clock())
                       if item["device_id"] == device_id and item["source"] == "hardware"
                       and item["connection_state"] == "online"), None)
        if not device:
            raise HTTPException(404, "Connected hardware device not found")
        with app.state.alert_test_lock:
            app.state.alert_test_commands[device_id] = (state, time.monotonic() + 20.0)
        return {"device_id": device_id, "state": state, "expires_in_seconds": 20}

    @app.delete("/v1/devices/{device_id}/alert-test", dependencies=[Depends(dashboard_auth)],
                tags=["dashboard"])
    def clear_device_alert_test(device_id: str):
        with app.state.alert_test_lock:
            app.state.alert_test_commands.pop(device_id, None)
        return {"device_id": device_id, "state": None}

    @app.get("/v1/device/{device_id}/alert-test", dependencies=[Depends(device_auth)],
             tags=["devices"])
    def device_alert_test(device_id: str):
        with app.state.alert_test_lock:
            command = app.state.alert_test_commands.get(device_id)
            if command and command[1] <= time.monotonic():
                app.state.alert_test_commands.pop(device_id, None)
                command = None
        now = clock()
        device = next((item for item in store.devices(now) if item["device_id"] == device_id), None)
        assessment = device["latest"]["assessment"] if device else None
        return {
            "state": command[0] if command else None,
            "risk_state": device["risk_state"] if device else None,
            "site_uv_index": assessment.get("site_uv_index") if assessment else None,
            "combined_risk_score": assessment.get("combined_risk_score") if assessment else None,
            "assessment_updated_at": device["latest"]["received_at"] if device else None,
        }

    @app.get("/v1/snapshot", response_model=Snapshot, dependencies=[Depends(dashboard_auth)], tags=["dashboard"])
    def snapshot():
        now = clock()
        store.mark_offline(now)
        alerts = store.alerts(limit=1001)
        return {"devices": store.devices(now), "alerts": alerts[:1000],
                "alerts_truncated": len(alerts) > 1000, "server_time": iso(now)}

    @app.patch("/v1/devices/{device_id}", dependencies=[Depends(dashboard_auth)], tags=["dashboard"])
    def update_device(device_id: str, update: DeviceUpdate):
        store.update_device(device_id, update.model_dump(exclude_unset=True))
        return {"device_id": device_id, **update.model_dump(exclude_unset=True)}

    @app.get("/v1/devices/{device_id}/history", response_model=HistoryPage, dependencies=[Depends(dashboard_auth)], tags=["dashboard"])
    def history(device_id: str, after_id: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
        items = store.history(device_id, after_id, limit)
        return {"items": items, "next_after_id": items[-1]["id"] if items else after_id}

    @app.get("/v1/devices/{device_id}/prediction", dependencies=[Depends(dashboard_auth)], tags=["dashboard"])
    def prediction(device_id: str, hours: int = Query(12, ge=1, le=24)):
        now = clock()
        store.mark_offline(now)
        device = next((item for item in store.devices(now) if item["device_id"] == device_id), None)
        if device is None:
            raise HTTPException(404, "Device not found")
        readings = store.recent_readings(device_id, 120)
        return build_prediction(device, readings, weather.get(), now, hours)

    @app.get("/v1/alerts", response_model=AlertPage, dependencies=[Depends(dashboard_auth)], tags=["dashboard"])
    def alerts(active_only: bool = True, after_id: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
        items = store.alerts(active_only, after_id, limit)
        return {"items": items, "next_after_id": items[-1]["id"] if items else after_id}

    @app.post("/v1/alerts/{alert_id}/ack", response_model=Alert, dependencies=[Depends(dashboard_auth)], tags=["dashboard"])
    def acknowledge(alert_id: int, body: AlertAcknowledgment):
        return store.acknowledge(alert_id, body.acknowledged_by, clock())

    return app
