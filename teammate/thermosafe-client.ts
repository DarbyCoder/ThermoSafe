// No dependencies. Works with browser fetch and modern Node.js.
export type RiskState = "unknown" | "normal" | "caution" | "warning" | "critical";
export type PulseQuality = "disabled" | "unvalidated" | "insufficient_signal" | "experimental" | "simulated";
export type DemoScenario = "normal" | "caution" | "warning" | "critical" | "unknown";
export interface MeshTopologyNode {
  nodeId: number;
  root?: boolean;
  subs?: MeshTopologyNode[];
}
export interface Assessment {
  state: RiskState;
  heat_index_c: number | null;
  site_uv_index: number | null;
  uv_index_score: number | null;
  heat_index_category: "unavailable" | "lower" | "caution" | "extreme_caution" | "danger" | "extreme_danger";
  indicator_f: number | null;
  bright_light_exposure_score: number;
  heat_duration_risk_score: number;
  exposure_risk_score: number;
  combined_risk_score: number;
  demo_score_components: Record<string, number | null>;
  score_contributions: Record<string, number | null>;
  triggered_by: string[];
  factor_details: string[];
  explanation: string;
  recommended_action: string;
  activity_level: "unknown" | "light" | "moderate" | "heavy";
  acclimatization_status: "unknown" | "acclimatized" | "not_acclimatized";
  model_version: string;
  reason: string;
}
export interface Reading {
  id: number;
  received_at: string;
  schema_version: 1;
  device_id: string;
  boot_id: string;
  sequence: number;
  uptime_ms: number;
  source: "hardware" | "simulator";
  firmware_version: string;
  environment_sensor_ok: boolean;
  temperature_c: number | null;
  humidity_pct: number | null;
  pulse_raw: number | null;
  pulse_bpm: number | null;
  pulse_quality: PulseQuality;
  light_sensor_ok: boolean;
  light_raw: number | null;
  light_intensity_pct: number | null;
  bright_light_threshold_pct: number | null;
  bright_light_active: boolean;
  bright_exposure_seconds: number;
  bright_continuous_seconds: number;
  heat_exposure_seconds: number;
  heat_continuous_seconds: number;
  activity_level: "unknown" | "light" | "moderate" | "heavy";
  acclimatization_status: "unknown" | "acclimatized" | "not_acclimatized";
  wifi_rssi_dbm: number | null;
  latitude_deg: number | null;
  longitude_deg: number | null;
  location_source: "unavailable" | "gps" | "wifi_estimate" | "simulated";
  mesh_role: "disabled" | "worker" | "gateway";
  mesh_connected: boolean;
  mesh_node_id: number | null;
  mesh_node_ids: number[];
  mesh_topology: MeshTopologyNode | null;
  assessment: Assessment;
}
export interface Device {
  device_id: string;
  worker_name: string | null;
  site: string | null;
  source: "hardware" | "simulator";
  connection_state: "online" | "offline";
  risk_state: RiskState;
  environment_sensor_ok: boolean | null;
  last_seen_at: string;
  age_seconds: number;
  latest: Reading;
}
export interface Alert {
  id: number;
  device_id: string;
  kind: "heat" | "environment_sensor" | "offline";
  severity: "warning" | "critical";
  active: boolean;
  opened_at: string;
  updated_at: string;
  resolved_at: string | null;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
}
export interface Snapshot {
  devices: Device[];
  alerts: Alert[];
  alerts_truncated: boolean;
  server_time: string;
}
export interface Page<T> { items: T[]; next_after_id: number }
export interface ApiConfig {
  schema_version: number;
  model_version: string;
  heat_index_bands_c: Record<string, Record<string, number>>;
  demo_score_weights: Record<string, number>;
  combined_risk_score_thresholds: { caution: number; warning: number; critical: number };
  offline_seconds: number;
  poll_interval_ms: number;
  expected_telemetry_interval_ms: number;
  pulse_used_for_risk: boolean;
  weather: { provider: string; location: string; latitude: number; longitude: number;
    uv_index_in_risk_assessment: boolean; uv_index_score_saturation: number };
  tigerdata_upload: { enabled: boolean; table: string; retry_queue: string };
  limitations: string;
}
export interface TigerDataStatus {
  configured: boolean;
  state: "not_configured" | "connecting" | "retrying" | "connected";
  last_success_at: string | null;
  last_error: string | null;
  pending_count: number;
  failed_pending_count: number;
  last_synced_at: string | null;
}
export interface SiteWeather {
  available: boolean;
  provider: string;
  location: string;
  latitude: number;
  longitude: number;
  fetched_at: string | null;
  stale?: boolean;
  current: { time: string | null; temperature_c: number | null;
    humidity_pct: number | null; uv_index: number | null } | null;
  hourly: Array<{ time: string; temperature_c: number; humidity_pct: number; uv_index: number }>;
  uv_metric: string;
  attribution?: string;
  unavailable_reason?: string;
}
export interface DevicePrediction {
  device_id: string;
  worker_name: string | null;
  source: "hardware" | "simulator";
  current_heat_index_estimate_c: number | null;
  current_reading_age_seconds: number | null;
  current_conditions: { temperature_c: number | null; humidity_pct: number | null;
    risk_state: RiskState; estimated_bright_light_exposure_seconds: number;
    elevated_heat_exposure_seconds: number; activity_level: Reading["activity_level"];
    acclimatization_status: Reading["acclimatization_status"] };
  next_threshold: { heat_index_c: number; category: string } | null;
  sensor_trend: { available: boolean; slope_c_per_minute: number | null;
    crossing_in_minutes: number | null; reason: string | null; samples_used?: number;
    lookback_seconds?: number };
  weather_forecast: { available: boolean; crossing_at: string | null;
    crossing_in_minutes: number | null; provider: string; reason?: string };
  earliest_crossing_basis: "sensor_trend" | "weather_forecast" | null;
  method: string;
  recommended_action: string;
  horizon_hours: number;
}
export interface DemoScenarioResult {
  scenario: DemoScenario;
  device_id: string;
  source: "simulator";
  sample: Omit<Reading, "id" | "received_at" | "assessment">;
  telemetry_id: number;
  received_at: string;
  assessment: Assessment;
}
export class ThermoSafeError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(`ThermoSafe API error: ${status}`);
  }
}
export class ThermoSafeClient {
  private baseUrl: string;
  constructor(baseUrl: string, private dashboardKey: string) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }
  private async request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(this.baseUrl + path, {
        method, signal: controller.signal, cache: "no-store",
        headers: { "X-Dashboard-Key": this.dashboardKey,
          ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const data: unknown = await response.json();
      if (!response.ok) throw new ThermoSafeError(response.status, data);
      return data as T;
    } finally {
      clearTimeout(timeout);
    }
  }
  getSnapshot(): Promise<Snapshot> { return this.request("/v1/snapshot"); }
  getConfig(): Promise<ApiConfig> { return this.request("/v1/config"); }
  getSiteWeather(): Promise<SiteWeather> { return this.request("/v1/weather"); }
  getTigerDataStatus(): Promise<TigerDataStatus> { return this.request("/v1/tigerdata/status"); }
  getDevices(): Promise<{ items: Device[]; server_time: string }> { return this.request("/v1/devices"); }
  createDemoScenario(scenario: DemoScenario): Promise<DemoScenarioResult> {
    return this.request(`/v1/demo/scenarios/${scenario}`, "POST");
  }
  previewDeviceAlert(deviceId: string, state: DemoScenario): Promise<{
    device_id: string; state: DemoScenario; expires_in_seconds: number;
  }> {
    return this.request(`/v1/devices/${encodeURIComponent(deviceId)}/alert-test/${state}`, "POST");
  }
  clearDeviceAlertPreview(deviceId: string): Promise<{ device_id: string; state: null }> {
    return this.request(`/v1/devices/${encodeURIComponent(deviceId)}/alert-test`, "DELETE");
  }
  getHistory(deviceId: string, afterId = 0, limit = 200): Promise<Page<Reading>> {
    return this.request(`/v1/devices/${encodeURIComponent(deviceId)}/history?after_id=${afterId}&limit=${limit}`);
  }
  getPrediction(deviceId: string, hours = 12): Promise<DevicePrediction> {
    return this.request(`/v1/devices/${encodeURIComponent(deviceId)}/prediction?hours=${hours}`);
  }
  getAlerts(activeOnly = true, afterId = 0, limit = 200): Promise<Page<Alert>> {
    return this.request(`/v1/alerts?active_only=${activeOnly}&after_id=${afterId}&limit=${limit}`);
  }
  acknowledgeAlert(alertId: number, acknowledgedBy: string): Promise<Alert> {
    return this.request(`/v1/alerts/${alertId}/ack`, "POST", { acknowledged_by: acknowledgedBy });
  }
  updateDevice(deviceId: string, update: { worker_name?: string | null; site?: string | null }): Promise<{
    device_id: string; worker_name?: string | null; site?: string | null;
  }> {
    return this.request(`/v1/devices/${encodeURIComponent(deviceId)}`, "PATCH", update);
  }
}
