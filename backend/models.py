from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator, StringConstraints

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class Telemetry(Model):
    schema_version: Literal[1] = 1
    device_id: Identifier
    boot_id: Identifier
    sequence: int = Field(ge=0, le=9_007_199_254_740_991)
    uptime_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    source: Literal["hardware", "simulator"] = "hardware"
    firmware_version: Annotated[str, StringConstraints(min_length=1, max_length=40)]
    measurement_mode: Literal["sensor", "simulated_demo"] = "sensor"
    demo_scenario_simulated: bool = False
    demo_scenario: Literal["green", "yellow", "red"] | None = None
    temperature_c: float | None = Field(default=None, ge=-40, le=85)
    humidity_pct: float | None = Field(default=None, ge=0, le=100)
    environment_sensor_ok: bool
    heat_index_c: float | None = Field(default=None, ge=-40, le=100)
    heat_index_category: Literal["unavailable", "lower", "caution", "extreme_caution", "danger", "extreme_danger"] = "unavailable"
    site_uv_index: float | None = Field(default=None, ge=0, le=30)
    site_uv_index_source: Literal["weather", "simulated_demo", "unavailable"] = "unavailable"
    risk_score_demo: float | None = Field(default=None, ge=0, le=100)
    alert_level: Literal["unknown", "normal", "caution", "warning", "critical"] = "unknown"
    alert_mode: Literal["auto", "green", "yellow", "red"] = "auto"
    alert_explanation: Annotated[str, StringConstraints(max_length=600)] = ""
    recommended_action: Annotated[str, StringConstraints(max_length=600)] = ""
    pulse_raw: int | None = Field(default=None, ge=0, le=4095)
    pulse_bpm: float | None = Field(default=None, ge=30, le=220)
    pulse_quality: Literal["disabled", "unvalidated", "insufficient_signal", "experimental", "simulated"] = "disabled"
    light_sensor_ok: bool = False
    light_raw: int | None = Field(default=None, ge=0, le=4095)
    light_intensity_pct: float | None = Field(default=None, ge=0, le=100,
        description="Relative photoresistor brightness percentage; not lux, UV, or solar radiation.")
    bright_light_threshold_pct: float | None = Field(default=None, gt=0, lt=100,
        validation_alias=AliasChoices("bright_light_threshold_pct", "sunlight_threshold_pct"),
        description="Calibrated relative-brightness threshold used to time estimated bright-light exposure.")
    bright_light_active: bool = Field(default=False,
        validation_alias=AliasChoices("bright_light_active", "sunlight_active"))
    bright_exposure_seconds: int = Field(default=0, ge=0, le=315_360_000,
        description="Cumulative estimated bright-light exposure duration for this shift.")
    bright_continuous_seconds: int = Field(default=0, ge=0, le=315_360_000)
    heat_exposure_seconds: int = Field(default=0, ge=0, le=315_360_000)
    heat_continuous_seconds: int = Field(default=0, ge=0, le=315_360_000)
    activity_level: Literal["unknown", "light", "moderate", "heavy"] = "unknown"
    acclimatization_status: Literal["unknown", "acclimatized", "not_acclimatized"] = "unknown"
    wifi_rssi_dbm: int | None = Field(default=None, ge=-127, le=0)
    latitude_deg: float | None = Field(default=None, ge=-90, le=90)
    longitude_deg: float | None = Field(default=None, ge=-180, le=180)
    location_source: Literal["unavailable", "gps", "wifi_estimate", "simulated"] = "unavailable"
    mesh_role: Literal["disabled", "worker", "gateway"] = "disabled"
    mesh_connected: bool = False
    mesh_node_id: int | None = Field(default=None, ge=0, le=4_294_967_295)
    mesh_node_ids: list[int] = Field(default_factory=list, max_length=32)
    mesh_topology: dict[str, object] | None = None

    @model_validator(mode="after")
    def consistent_readings(self):
        has_environment = self.temperature_c is not None and self.humidity_pct is not None
        if self.environment_sensor_ok != has_environment:
            raise ValueError("Sensor OK requires both readings; sensor error requires both readings null")
        if self.demo_scenario_simulated != (self.measurement_mode == "simulated_demo"):
            raise ValueError("Demo flag and measurement mode must agree")
        if self.demo_scenario_simulated != (self.demo_scenario is not None):
            raise ValueError("A simulated demo payload must name its scenario")
        if not self.environment_sensor_ok and (self.temperature_c is not None or self.humidity_pct is not None):
            raise ValueError("Failed environment readings must both be null")
        if self.pulse_bpm is not None and self.pulse_quality not in ("experimental", "simulated"):
            raise ValueError("Any pulse estimate must be marked experimental or simulated")
        if self.pulse_quality == "simulated" and self.pulse_bpm is None:
            raise ValueError("Simulated pulse quality requires a simulated BPM value")
        if self.pulse_quality == "disabled" and (self.pulse_raw is not None or self.pulse_bpm is not None):
            raise ValueError("Disabled pulse sensor must have null readings")
        has_light = (self.light_raw is not None and self.light_intensity_pct is not None
                     and self.bright_light_threshold_pct is not None)
        if self.light_sensor_ok != has_light:
            raise ValueError("Light sensor OK requires ADC, intensity, and threshold readings")
        if not self.light_sensor_ok and self.bright_light_active:
            raise ValueError("Bright-light condition cannot be active when the light sensor is disabled")
        has_location = self.latitude_deg is not None and self.longitude_deg is not None
        if (self.latitude_deg is None) != (self.longitude_deg is None):
            raise ValueError("Latitude and longitude must both be present or both be null")
        if has_location == (self.location_source == "unavailable"):
            raise ValueError("Location source must match coordinate availability")
        if any(node_id < 0 or node_id > 4_294_967_295 for node_id in self.mesh_node_ids):
            raise ValueError("Mesh node IDs must be unsigned 32-bit values")
        if self.mesh_role == "disabled" and (self.mesh_connected or self.mesh_node_id is not None):
            raise ValueError("Disabled mesh must not report an active node")
        if self.mesh_role != "disabled" and self.mesh_node_id is None:
            raise ValueError("Enabled mesh requires this device's mesh node ID")
        return self


class DeviceUpdate(Model):
    worker_name: Label | None = None
    site: Label | None = None


class AlertAcknowledgment(Model):
    acknowledged_by: Label


RiskState = Literal["unknown", "normal", "caution", "warning", "critical"]


class Assessment(Model):
    state: RiskState
    heat_index_c: float | None = Field(description="NOAA/NWS apparent temperature in Celsius when calculable.")
    site_uv_index: float | None = Field(default=None, description="Current site-level UV index forecast, not a wearable measurement.")
    uv_index_score: float | None = Field(default=None, description="Demo UV component score, saturated at UV index 11.")
    heat_index_category: Literal["unavailable", "lower", "caution", "extreme_caution", "danger", "extreme_danger"] = "unavailable"
    indicator_f: float | None
    bright_light_exposure_score: float = Field(default=0,
        validation_alias=AliasChoices("bright_light_exposure_score", "sunlight_risk_score"))
    heat_duration_risk_score: float = 0
    exposure_risk_score: float = 0
    combined_risk_score: float = 0
    demo_score_components: dict[str, float | None] = Field(default_factory=dict)
    score_contributions: dict[str, float | None] = Field(default_factory=dict)
    triggered_by: list[str] = Field(default_factory=list)
    factor_details: list[str] = Field(default_factory=list)
    explanation: str = ""
    recommended_action: str = ""
    activity_level: Literal["unknown", "light", "moderate", "heavy"] = "unknown"
    acclimatization_status: Literal["unknown", "acclimatized", "not_acclimatized"] = "unknown"
    model_version: str
    reason: str


class Receipt(Model):
    telemetry_id: int
    received_at: str
    duplicate: bool
    assessment: Assessment


class DemoScenarioResult(Model):
    scenario: Literal["normal", "caution", "warning", "critical", "unknown"]
    device_id: Identifier
    source: Literal["simulator"]
    sample: Telemetry
    telemetry_id: int
    received_at: str
    assessment: Assessment


class StoredReading(Telemetry):
    id: int
    received_at: str
    assessment: Assessment


class Device(Model):
    device_id: str
    worker_name: str | None
    site: str | None
    source: Literal["hardware", "simulator"]
    connection_state: Literal["online", "offline"]
    risk_state: RiskState
    environment_sensor_ok: bool | None
    last_seen_at: str
    age_seconds: float
    latest: StoredReading


class Alert(Model):
    id: int
    device_id: str
    kind: Literal["heat", "environment_sensor", "offline"]
    severity: Literal["warning", "critical"]
    opened_at: str
    updated_at: str
    resolved_at: str | None
    acknowledged_at: str | None
    acknowledged_by: str | None
    active: bool


class Snapshot(Model):
    devices: list[Device]
    alerts: list[Alert]
    alerts_truncated: bool
    server_time: str


class DeviceList(Model):
    items: list[Device]
    server_time: str


class TigerDataStatus(Model):
    configured: bool
    state: Literal["not_configured", "connecting", "retrying", "connected"]
    last_success_at: str | None
    last_error: str | None
    pending_count: int
    failed_pending_count: int
    last_synced_at: str | None


class HistoryPage(Model):
    items: list[StoredReading]
    next_after_id: int


class AlertPage(Model):
    items: list[Alert]
    next_after_id: int
