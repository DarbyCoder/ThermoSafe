"""Transactional SQLite store; one connection per operation."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .models import Telemetry
from .risk import assess


class Conflict(Exception):
    pass


class Missing(Exception):
    pass


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


class Store:
    def __init__(self, path: Path, offline_seconds: float):
        self.path = path
        self.offline_seconds = offline_seconds

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError(f"Unsupported database schema {version}")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY, source TEXT NOT NULL,
                    worker_name TEXT, site TEXT, last_seen REAL NOT NULL,
                    active_boot_id TEXT NOT NULL, last_sequence INTEGER NOT NULL,
                    last_uptime_ms INTEGER NOT NULL, last_telemetry_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS boots (
                    device_id TEXT NOT NULL REFERENCES devices(device_id), boot_id TEXT NOT NULL,
                    PRIMARY KEY(device_id, boot_id)
                );
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL REFERENCES devices(device_id),
                    boot_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    received_at REAL NOT NULL, payload TEXT NOT NULL, assessment TEXT NOT NULL,
                    UNIQUE(device_id, boot_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS telemetry_device_id ON telemetry(device_id, id);
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL REFERENCES devices(device_id),
                    kind TEXT NOT NULL, severity TEXT NOT NULL,
                    opened_at REAL NOT NULL, updated_at REAL NOT NULL, resolved_at REAL,
                    acknowledged_at REAL, acknowledged_by TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS alert_one_active
                    ON alerts(device_id, kind) WHERE resolved_at IS NULL;
                CREATE TABLE IF NOT EXISTS tiger_outbox (
                    telemetry_id INTEGER PRIMARY KEY REFERENCES telemetry(id) ON DELETE CASCADE,
                    synced_at REAL,
                    last_attempt_at REAL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                );
                INSERT OR IGNORE INTO tiger_outbox(telemetry_id)
                    SELECT id FROM telemetry;
                PRAGMA user_version=1;
            """)

    @staticmethod
    def _open_alert(conn, device_id, kind, severity, now):
        row = conn.execute("SELECT * FROM alerts WHERE device_id=? AND kind=? AND resolved_at IS NULL",
                           (device_id, kind)).fetchone()
        if row is None:
            conn.execute("INSERT INTO alerts(device_id,kind,severity,opened_at,updated_at) VALUES(?,?,?,?,?)",
                         (device_id, kind, severity, now, now))
        elif row["severity"] != severity:
            # A previous acknowledgment must not hide a new escalation.
            escalating = severity == "critical"
            conn.execute("""UPDATE alerts SET severity=?,updated_at=?,
                acknowledged_at=CASE WHEN ? THEN NULL ELSE acknowledged_at END,
                acknowledged_by=CASE WHEN ? THEN NULL ELSE acknowledged_by END WHERE id=?""",
                         (severity, now, escalating, escalating, row["id"]))

    @staticmethod
    def _resolve_alert(conn, device_id, kind, now):
        conn.execute("UPDATE alerts SET resolved_at=?,updated_at=? WHERE device_id=? AND kind=? AND resolved_at IS NULL",
                     (now, now, device_id, kind))

    def ingest(self, reading: Telemetry, now: float, site_uv_index: float | None = None) -> dict:
        payload = reading.model_dump()
        assessment = assess(reading.temperature_c, reading.humidity_pct,
                            reading.bright_exposure_seconds,
                            reading.heat_exposure_seconds,
                            reading.activity_level,
                            reading.acclimatization_status,
                            site_uv_index)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            device = conn.execute("SELECT * FROM devices WHERE device_id=?", (reading.device_id,)).fetchone()
            if device is not None and device["source"] != reading.source:
                raise Conflict("A device ID cannot switch between hardware and simulator")
            previous = conn.execute("SELECT * FROM telemetry WHERE device_id=? AND boot_id=? AND sequence=?",
                                    (reading.device_id, reading.boot_id, reading.sequence)).fetchone()
            if previous:
                if json.loads(previous["payload"]) != payload:
                    raise Conflict("Sequence reused with a different payload")
                return self._receipt(previous, duplicate=True)
            if device:
                if device["active_boot_id"] == reading.boot_id:
                    if reading.sequence <= device["last_sequence"] or reading.uptime_ms < device["last_uptime_ms"]:
                        raise Conflict("Out-of-order sequence or uptime within a boot")
                elif conn.execute("SELECT 1 FROM boots WHERE device_id=? AND boot_id=?",
                                  (reading.device_id, reading.boot_id)).fetchone():
                    raise Conflict("Telemetry from a retired boot")
            else:
                conn.execute("""INSERT INTO devices(device_id,source,last_seen,active_boot_id,last_sequence,last_uptime_ms)
                    VALUES(?,?,?,?,?,?)""", (reading.device_id, reading.source, now, reading.boot_id,
                                            reading.sequence, reading.uptime_ms))
            conn.execute("INSERT OR IGNORE INTO boots VALUES(?,?)", (reading.device_id, reading.boot_id))
            cur = conn.execute("INSERT INTO telemetry(device_id,boot_id,sequence,received_at,payload,assessment) VALUES(?,?,?,?,?,?)",
                               (reading.device_id, reading.boot_id, reading.sequence, now,
                                json.dumps(payload, allow_nan=False), json.dumps(assessment, allow_nan=False)))
            conn.execute("INSERT INTO tiger_outbox(telemetry_id) VALUES(?)", (cur.lastrowid,))
            conn.execute("""UPDATE devices SET last_seen=?,active_boot_id=?,last_sequence=?,last_uptime_ms=?,
                last_telemetry_id=? WHERE device_id=?""",
                         (now, reading.boot_id, reading.sequence, reading.uptime_ms, cur.lastrowid, reading.device_id))
            self._resolve_alert(conn, reading.device_id, "offline", now)
            if reading.environment_sensor_ok:
                self._resolve_alert(conn, reading.device_id, "environment_sensor", now)
            else:
                self._open_alert(conn, reading.device_id, "environment_sensor", "warning", now)
                # A sensor failure cannot establish that a previous heat warning ended.
            if assessment["state"] in ("warning", "critical"):
                self._open_alert(conn, reading.device_id, "heat", assessment["state"], now)
            elif reading.environment_sensor_ok:
                self._resolve_alert(conn, reading.device_id, "heat", now)
            row = conn.execute("SELECT * FROM telemetry WHERE id=?", (cur.lastrowid,)).fetchone()
            return self._receipt(row, duplicate=False)

    @staticmethod
    def _receipt(row, duplicate):
        return {"telemetry_id": row["id"], "received_at": iso(row["received_at"]),
                "duplicate": duplicate, "assessment": json.loads(row["assessment"])}

    def mark_offline(self, now: float):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in conn.execute("SELECT device_id FROM devices WHERE last_seen<=?", (now-self.offline_seconds,)):
                self._open_alert(conn, row["device_id"], "offline", "warning", now)

    @staticmethod
    def _reading(row):
        return {"id": row["id"], "received_at": iso(row["received_at"]),
                **json.loads(row["payload"]), "assessment": json.loads(row["assessment"])}

    def devices(self, now: float):
        with self.connection() as conn:
            rows = conn.execute("""SELECT d.*,t.id,t.received_at,t.payload,t.assessment
                FROM devices d JOIN telemetry t ON t.id=d.last_telemetry_id ORDER BY d.device_id""").fetchall()
            result = []
            for row in rows:
                online = now-row["last_seen"] < self.offline_seconds
                assessment = json.loads(row["assessment"])
                payload = json.loads(row["payload"])
                result.append({"device_id": row["device_id"], "worker_name": row["worker_name"],
                               "site": row["site"], "source": row["source"],
                               "connection_state": "online" if online else "offline",
                               "risk_state": assessment["state"] if online else "unknown",
                               "environment_sensor_ok": payload["environment_sensor_ok"] if online else None,
                               "last_seen_at": iso(row["last_seen"]),
                               "age_seconds": round(max(0, now-row["last_seen"]), 2),
                               "latest": self._reading(row)})
            return result

    @staticmethod
    def _alert(row):
        result = dict(row)
        for key in ("opened_at", "updated_at", "resolved_at", "acknowledged_at"):
            result[key] = iso(result[key]) if result[key] is not None else None
        result["active"] = row["resolved_at"] is None
        return result

    def alerts(self, active_only=True, after_id=0, limit=200):
        with self.connection() as conn:
            query = "SELECT * FROM alerts WHERE id>?"
            if active_only:
                query += " AND resolved_at IS NULL"
            rows = conn.execute(query + " ORDER BY id ASC LIMIT ?", (after_id, limit)).fetchall()
            return [self._alert(row) for row in rows]

    def history(self, device_id, after_id, limit):
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM devices WHERE device_id=?", (device_id,)).fetchone():
                raise Missing("Device not found")
            rows = conn.execute("SELECT * FROM telemetry WHERE device_id=? AND id>? ORDER BY id ASC LIMIT ?",
                                (device_id, after_id, limit)).fetchall()
            return [self._reading(row) for row in rows]

    def recent_readings(self, device_id, limit=120):
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM devices WHERE device_id=?", (device_id,)).fetchone():
                raise Missing("Device not found")
            rows = conn.execute(
                "SELECT * FROM telemetry WHERE device_id=? ORDER BY id DESC LIMIT ?",
                (device_id, limit),
            ).fetchall()
            return [self._reading(row) for row in reversed(rows)]

    def pending_tiger_uploads(self, limit=100):
        with self.connection() as conn:
            rows = conn.execute("""
                SELECT t.id, t.received_at, t.payload, t.assessment,
                       d.worker_name, d.site
                FROM tiger_outbox o
                JOIN telemetry t ON t.id=o.telemetry_id
                JOIN devices d ON d.device_id=t.device_id
                WHERE o.synced_at IS NULL
                ORDER BY t.id ASC LIMIT ?
            """, (limit,)).fetchall()
            return [{"id": row["id"], "received_at": iso(row["received_at"]),
                     "payload": json.loads(row["payload"]),
                     "assessment": json.loads(row["assessment"]),
                     "worker_name": row["worker_name"], "site": row["site"]}
                    for row in rows]

    def mark_tiger_uploads_synced(self, telemetry_ids, now):
        if not telemetry_ids:
            return
        placeholders = ",".join("?" for _ in telemetry_ids)
        with self.connection() as conn:
            conn.execute(f"UPDATE tiger_outbox SET synced_at=?,last_error=NULL WHERE telemetry_id IN ({placeholders})",
                         (now, *telemetry_ids))

    def mark_tiger_uploads_failed(self, telemetry_ids, now, error):
        if not telemetry_ids:
            return
        placeholders = ",".join("?" for _ in telemetry_ids)
        with self.connection() as conn:
            conn.execute(f"""UPDATE tiger_outbox SET last_attempt_at=?,attempt_count=attempt_count+1,
                last_error=? WHERE telemetry_id IN ({placeholders})""",
                         (now, error, *telemetry_ids))

    def tiger_outbox_status(self):
        with self.connection() as conn:
            pending = conn.execute("SELECT COUNT(*) FROM tiger_outbox WHERE synced_at IS NULL").fetchone()[0]
            synced_at = conn.execute("SELECT MAX(synced_at) FROM tiger_outbox").fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM tiger_outbox WHERE synced_at IS NULL AND attempt_count>0").fetchone()[0]
            last_error = conn.execute("""SELECT last_error FROM tiger_outbox
                WHERE synced_at IS NULL AND last_error IS NOT NULL
                ORDER BY last_attempt_at DESC LIMIT 1""").fetchone()
            return {"pending_count": pending, "failed_pending_count": failed,
                    "last_synced_at": iso(synced_at) if synced_at is not None else None,
                    "last_error": last_error[0] if last_error else None}

    def update_device(self, device_id, changes):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM devices WHERE device_id=?", (device_id,)).fetchone():
                raise Missing("Device not found")
            for field, value in changes.items():
                if field not in ("worker_name", "site"):
                    raise ValueError("Invalid device metadata field")
                conn.execute(f"UPDATE devices SET {field}=? WHERE device_id=?", (value, device_id))

    def acknowledge(self, alert_id, name, now):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
            if row is None:
                raise Missing("Alert not found")
            if row["resolved_at"] is not None:
                raise Conflict("Alert already resolved")
            if row["acknowledged_at"] is None:
                conn.execute("UPDATE alerts SET acknowledged_at=?,acknowledged_by=?,updated_at=? WHERE id=?",
                             (now, name, now, alert_id))
            return self._alert(conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone())
