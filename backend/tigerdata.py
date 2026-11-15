"""Asynchronous-outbox delivery into a Tiger Cloud PostgreSQL hypertable."""

from datetime import datetime, timezone
from threading import Lock


DEVICE_SQL = """
INSERT INTO thermosafe_devices (device_id, worker_name, site, source, updated_at)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (device_id) DO UPDATE SET
    worker_name = EXCLUDED.worker_name,
    site = EXCLUDED.site,
    source = EXCLUDED.source,
    updated_at = EXCLUDED.updated_at
"""

TELEMETRY_SQL = """
INSERT INTO thermosafe_telemetry (
    received_at, device_id, worker_name, site, source, boot_id, sequence,
    uptime_ms, firmware_version, schema_version, temperature_c, humidity_pct,
    environment_sensor_ok, pulse_raw, pulse_bpm, pulse_quality,
    light_sensor_ok, light_raw, light_intensity_pct, bright_light_threshold_pct,
    bright_light_active, bright_exposure_seconds, bright_continuous_seconds,
    heat_exposure_seconds, heat_continuous_seconds, activity_level,
    acclimatization_status, wifi_rssi_dbm, heat_index_c, heat_index_category,
    site_uv_index, uv_index_score, risk_state, combined_risk_score,
    explanation, recommended_action, latitude_deg, longitude_deg, location_source,
    mesh_role, mesh_connected, mesh_node_id, mesh_node_ids, mesh_topology, payload, assessment
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (received_at, device_id, boot_id, sequence) DO NOTHING
"""


class TigerDataSink:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._lock = Lock()
        self._schema_ready = False
        self._last_success_at: str | None = None
        self._last_error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self._dsn)

    def status(self) -> dict:
        with self._lock:
            if not self.configured:
                state = "not_configured"
            elif self._last_error:
                state = "retrying"
            elif self._last_success_at:
                state = "connected"
            else:
                state = "connecting"
            return {"configured": self.configured, "state": state,
                    "last_success_at": self._last_success_at,
                    "last_error": self._last_error}

    def _connect(self):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict

        parameters = conninfo_to_dict(self._dsn)
        sslmode = parameters.get("sslmode", "require")
        if sslmode not in ("require", "verify-ca", "verify-full"):
            raise ValueError("TigerData connection must use TLS (sslmode=require or stronger)")
        parameters["sslmode"] = sslmode
        parameters.setdefault("connect_timeout", 5)
        return psycopg.connect(**parameters)

    @staticmethod
    def _ensure_schema(conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS thermosafe_devices (
                device_id TEXT PRIMARY KEY,
                worker_name TEXT,
                site TEXT,
                source TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS thermosafe_telemetry (
                received_at TIMESTAMPTZ NOT NULL,
                device_id TEXT NOT NULL,
                worker_name TEXT,
                site TEXT,
                source TEXT NOT NULL,
                boot_id TEXT NOT NULL,
                sequence BIGINT NOT NULL,
                uptime_ms BIGINT NOT NULL,
                firmware_version TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                temperature_c DOUBLE PRECISION,
                humidity_pct DOUBLE PRECISION,
                environment_sensor_ok BOOLEAN NOT NULL,
                pulse_raw INTEGER,
                pulse_bpm DOUBLE PRECISION,
                pulse_quality TEXT NOT NULL,
                light_sensor_ok BOOLEAN NOT NULL,
                light_raw INTEGER,
                light_intensity_pct DOUBLE PRECISION,
                bright_light_threshold_pct DOUBLE PRECISION,
                bright_light_active BOOLEAN NOT NULL,
                bright_exposure_seconds BIGINT NOT NULL,
                bright_continuous_seconds BIGINT NOT NULL,
                heat_exposure_seconds BIGINT NOT NULL,
                heat_continuous_seconds BIGINT NOT NULL,
                activity_level TEXT NOT NULL,
                acclimatization_status TEXT NOT NULL,
                wifi_rssi_dbm INTEGER,
                latitude_deg DOUBLE PRECISION,
                longitude_deg DOUBLE PRECISION,
                location_source TEXT NOT NULL DEFAULT 'unavailable',
                mesh_role TEXT NOT NULL DEFAULT 'disabled',
                mesh_connected BOOLEAN NOT NULL DEFAULT FALSE,
                mesh_node_id BIGINT,
                mesh_node_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                mesh_topology JSONB,
                heat_index_c DOUBLE PRECISION,
                heat_index_category TEXT NOT NULL,
                site_uv_index DOUBLE PRECISION,
                uv_index_score DOUBLE PRECISION,
                risk_state TEXT NOT NULL,
                combined_risk_score DOUBLE PRECISION NOT NULL,
                explanation TEXT NOT NULL,
                recommended_action TEXT NOT NULL,
                payload JSONB NOT NULL,
                assessment JSONB NOT NULL,
                PRIMARY KEY (received_at, device_id, boot_id, sequence)
            ) WITH (tsdb.hypertable, tsdb.segmentby = 'device_id')
        """)
        # Existing TigerData tables may predate location and mesh telemetry.
        conn.execute("ALTER TABLE thermosafe_telemetry ADD COLUMN IF NOT EXISTS latitude_deg DOUBLE PRECISION")
        conn.execute("ALTER TABLE thermosafe_telemetry ADD COLUMN IF NOT EXISTS longitude_deg DOUBLE PRECISION")
        conn.execute("ALTER TABLE thermosafe_telemetry ADD COLUMN IF NOT EXISTS location_source TEXT NOT NULL DEFAULT 'unavailable'")
        conn.execute("ALTER TABLE thermosafe_telemetry ADD COLUMN IF NOT EXISTS mesh_role TEXT NOT NULL DEFAULT 'disabled'")
        conn.execute("ALTER TABLE thermosafe_telemetry ADD COLUMN IF NOT EXISTS mesh_connected BOOLEAN NOT NULL DEFAULT FALSE")
        conn.execute("ALTER TABLE thermosafe_telemetry ADD COLUMN IF NOT EXISTS mesh_node_id BIGINT")
        conn.execute("ALTER TABLE thermosafe_telemetry ADD COLUMN IF NOT EXISTS mesh_node_ids JSONB NOT NULL DEFAULT '[]'::jsonb")
        conn.execute("ALTER TABLE thermosafe_telemetry ADD COLUMN IF NOT EXISTS mesh_topology JSONB")

    @staticmethod
    def _timestamp(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)

    def upload_batch(self, rows: list[dict]) -> None:
        if not rows:
            return

        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            if not self._schema_ready:
                self._ensure_schema(conn)

            for row in rows:
                payload = row["payload"]
                assessment = row["assessment"]
                received_at = self._timestamp(row["received_at"])
                device_id = payload["device_id"]
                worker_name = row.get("worker_name")
                site = row.get("site")
                source = payload["source"]

                conn.execute(DEVICE_SQL, (device_id, worker_name, site, source, received_at))
                conn.execute(TELEMETRY_SQL, (
                    received_at, device_id, worker_name, site, source,
                    payload["boot_id"], payload["sequence"], payload["uptime_ms"],
                    payload["firmware_version"], payload["schema_version"],
                    payload["temperature_c"], payload["humidity_pct"],
                    payload["environment_sensor_ok"], payload["pulse_raw"],
                    payload["pulse_bpm"], payload["pulse_quality"],
                    payload["light_sensor_ok"], payload["light_raw"],
                    payload["light_intensity_pct"], payload["bright_light_threshold_pct"],
                    payload["bright_light_active"], payload["bright_exposure_seconds"],
                    payload["bright_continuous_seconds"], payload["heat_exposure_seconds"],
                    payload["heat_continuous_seconds"], payload["activity_level"],
                    payload["acclimatization_status"], payload["wifi_rssi_dbm"],
                    assessment.get("heat_index_c"), assessment.get("heat_index_category", "unavailable"),
                    assessment.get("site_uv_index"), assessment.get("uv_index_score"),
                    assessment.get("state", "unknown"), assessment.get("combined_risk_score", 0.0),
                    assessment.get("explanation", assessment.get("reason", "")),
                    assessment.get("recommended_action", ""),
                    payload.get("latitude_deg"), payload.get("longitude_deg"),
                    payload.get("location_source", "unavailable"),
                    payload.get("mesh_role", "disabled"), payload.get("mesh_connected", False),
                    payload.get("mesh_node_id"), Jsonb(payload.get("mesh_node_ids", [])),
                    Jsonb(payload["mesh_topology"]) if payload.get("mesh_topology") is not None else None,
                    Jsonb(payload), Jsonb(assessment),
                ))

        with self._lock:
            self._schema_ready = True
            self._last_success_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._last_error = None

    def record_error(self, error: Exception) -> None:
        # Keep only the exception class so connection strings or host details never enter logs/API output.
        with self._lock:
            self._last_error = type(error).__name__
