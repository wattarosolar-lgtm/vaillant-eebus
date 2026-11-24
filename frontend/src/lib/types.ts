// Mirrors the backend's dataclasses (pairing.py, state.py, write_gate.py, log_store.py, api.py,
// ha_import.py, home_assistant.py, history.py, settings.py, hems/).

export type ConnectionState =
  | 'not_paired'
  | 'bridge_unavailable'
  | 'ship_port_in_use'
  | 'foreign_bridge'
  | 'disconnected'
  | 'connected'
  | 'trust_removed'
  | 'failed'
  // Connection myVAILLANT: no entities chosen yet, or Home Assistant not reachable.
  | 'not_configured'
  | 'ha_unavailable';

/** How the app reaches the heat pump. */
export type Connection = 'myvaillant' | 'eebus';

/** The myVAILLANT integration's entities the app reads and controls. */
export interface MyVaillantEntities {
  /** Climate entities, one per zone; their IDs are the zones' IDs. */
  zones: string[];
  hot_water: string | null;
  outdoor_temperature: string | null;
  power: string | null;
}

export interface ConnectionStatus {
  /** Null until chosen in the setup wizard. */
  connection: Connection | null;
  /** The connection the app works with right now. */
  active: Connection;
  state: ConnectionState;
  myvaillant: MyVaillantEntities;
  home_assistant_available: boolean;
  app_version: string;
  /** The app restarts to change the connection. */
  restarting: boolean;
}

export interface Gateway {
  ski: string;
  brand: string;
  model: string;
  serial: string;
  device_type: string;
  host: string;
  software_revision: string;
  hardware_revision: string;
  name: string;
  ship_id: string;
  categories: number[];
}

export interface BridgeInfo {
  reachable: boolean;
  ski: string;
  build: string;
}

export interface PairingStatus {
  bridge: BridgeInfo;
  state: ConnectionState;
  paired: Gateway | null;
  discovered: Gateway[];
  app_version: string;
}

export interface Reading {
  value: number;
  unit: string;
  updated_at: string | null;
  source: string;
}

export interface Setpoint {
  value: number;
  min: number;
  max: number;
  step: number;
  writable: boolean;
}

export interface Zone {
  id: string;
  label: string;
  room_temperature: Reading | null;
  setpoint: Setpoint | null;
  mode: string | null;
  available_modes: string[];
  mode_writable: boolean;
  /** myVAILLANT: a temporary override (quick veto) runs, until this time if known. */
  override: boolean;
  override_until: string | null;
  /** myVAILLANT: the time program's setback ("eco") runs; the setpoint is the last one known. */
  setback: boolean;
}

export interface HotWater {
  temperature: Reading | null;
  setpoint: Setpoint | null;
  mode: string | null;
  available_modes: string[];
  mode_writable: boolean;
  boost: 'inactive' | 'active' | 'running' | 'finished' | null;
  boost_writable: boolean;
}

export interface HeatPumpState {
  available: boolean;
  connected: boolean;
  heartbeat_ok: boolean | null;
  operating_state: string | null;
  compressor_active: boolean | null;
  measurements: Partial<Record<string, Reading>>;
  zones: Zone[];
  hot_water: HotWater | null;
  captured_at: string | null;
}

export type OperatingMode = 'demo' | 'live';

export type Control =
  | 'zone_setpoint'
  | 'zone_mode'
  | 'dhw_setpoint'
  | 'dhw_mode'
  | 'dhw_boost'
  | 'consumption_limit'
  | 'failsafe_limit'
  | 'compressor';

/** A value sent in live mode that the device has not reported yet. */
export interface PendingWrite {
  control: Control;
  zone_id: string | null;
  value: number | string | boolean;
  message: string;
  since: string;
}

export interface WriteResult {
  sent: boolean;
  mode: OperatingMode;
  message: string;
  would_send: Record<string, string | number | boolean | null> | null;
}

/** Summed up by the app from the power; incomplete when part of the day is missing. */
export interface EnergyToday {
  kwh: number;
  incomplete: boolean;
}

/** ok: current; stale: older than allowed; unavailable: missing, not a number or unknown unit. */
export type ValueStatus = 'ok' | 'stale' | 'unavailable';

export interface RoomReading {
  entity_id: string;
  name: string;
  zone_id: string | null;
  celsius: number | null;
  status: ValueStatus;
  reported_at: string | null;
}

export interface ZoneAverage {
  zone_id: string;
  celsius: number | null;
  /** Rooms in the average, and rooms assigned to the zone. */
  rooms: number;
  of: number;
}

/** The heat of the day: the sum of the chosen sensors, e.g. heating and hot water. */
export interface HeatToday {
  entity_ids: string[];
  /** Null unless every sensor has a value. */
  kwh: number | null;
  status: ValueStatus;
  reported_at: string | null;
}

export interface HomeAssistantState {
  /** False outside Home Assistant (development). */
  available: boolean;
  connected: boolean;
  rooms: RoomReading[];
  zones: ZoneAverage[];
  heat_today: HeatToday | null;
}

export interface ApiState {
  connection: ConnectionState;
  /** Where the values come from. */
  link: Connection;
  mode: OperatingMode;
  pending: PendingWrite[];
  heat_pump: HeatPumpState | null;
  energy_today: EnergyToday | null;
  home_assistant: HomeAssistantState;
  /** Heat of the day divided by energy_today; null unless both are reliable. */
  performance_today: number | null;
}

/** Why there is no power history: outside Home Assistant, not exported, or Home Assistant down. */
export type HistoryGap = 'no_home_assistant' | 'not_exported' | 'unreachable';

/** Mean power per bucket over the last 24 hours, from Home Assistant's history (history.py). */
export interface PowerHistory {
  start: string | null;
  step_seconds: number;
  /** Oldest first; null where nothing was recorded. */
  watts: (number | null)[];
  mean_watts: number | null;
  peak_watts: number | null;
  gap: HistoryGap | null;
}

export type ExportGroup =
  | 'status'
  | 'zones'
  | 'hot_water'
  | 'temperatures'
  | 'energy'
  | 'power_limits'
  | 'compressor'
  | 'hems';

export interface Room {
  entity_id: string;
  name: string;
  zone_id: string | null;
}

export interface AppSettings {
  rooms: Room[];
  heat_energy_entities: string[];
  export: ExportGroup[];
  /** Entity IDs the app currently reports to Home Assistant. */
  exported: string[];
  home_assistant_available: boolean;
}

export interface EntityChoice {
  entity_id: string;
  name: string;
  state: string;
  unit: string | null;
}

export type LogCategory = 'mode' | 'write' | 'connection' | 'device' | 'settings' | 'hems' | 'rooms';

export interface LogEntry {
  id: number;
  time: string;
  category: LogCategory;
  message: string;
  /** A change did not take effect, or a connection broke. */
  problem: boolean;
}

/** What the protocol page lists: one category, only problems, or everything. */
export interface LogFilter {
  category: LogCategory | null;
  problems: boolean;
}

export interface LogPage {
  entries: LogEntry[];
  more: boolean;
}

/** What Home Assistant entities are offered for: rooms, heat, myVAILLANT and the energy management. */
export type EntityKind =
  | 'temperature'
  | 'energy'
  | 'power'
  | 'percent'
  | 'humidity'
  | 'window'
  | 'room'
  | 'thermostat'
  | 'weather'
  | 'climate'
  | 'water_heater';

// --- energy management (hems/) ---------------------------------------------------

/** Aus, Vorschau (plans, sends nothing); Steuern of the zones comes with 1.5.0. */
export type PlannerMode = 'off' | 'preview' | 'control';
export type WeatherSource = 'open_meteo' | 'home_assistant';
export type ZoneKind = 'floor' | 'radiator';
export type RiskLevel = 'none' | 'low' | 'medium' | 'high';
export type Supply = 'unknown' | 'supplied' | 'under' | 'over';
export type FlowHint = 'unknown' | 'hold' | 'higher' | 'lower';

export interface ZoneConfig {
  zone_id: string;
  kind: ZoneKind | null;
  flow_entity: string | null;
  flow_setpoint_entity: string | null;
  design_flow_c: number | null;
  design_return_c: number | null;
  band_down_k: number | null;
  band_up_k: number | null;
}

/** The settings as the backend stores them (hems_to_json). */
export interface HemsSettings {
  mode: PlannerMode;
  weather_source: WeatherSource;
  weather_entity: string | null;
  latitude: number | null;
  longitude: number | null;
  heat_heating_entity: string | null;
  heat_dhw_entity: string | null;
  buffer_entity: string | null;
  design_outdoor_c: number | null;
  heat_load_w: number | null;
  zones: ZoneConfig[];
  step_k: number;
  min_interval_min: number;
  /** "HH:MM", local. */
  night_start: string;
  night_end: string;
  night_loss_k: number;
  defrost_loss_k: number;
  target_starts: number;
  heating_limit_c: number;
  block_above_c: number;
  cloud_commands_max: number;
  zone1_threshold_w: number;
}

export interface WeatherHour {
  start: string;
  temperature_c: number | null;
  humidity: number | null;
  dew_point_c: number | null;
  wind_ms: number | null;
  radiation_w: number | null;
  cloud_cover: number | null;
  precipitation_mm: number | null;
  snowfall_cm: number | null;
}

export interface ForecastStatus {
  source: WeatherSource | null;
  fetched_at: string | null;
  calibrated: boolean;
  /** How well the day-ahead forecast held against the heat pump's sensor. */
  check: { hours: number; mean_abs_error_k: number; bias_k: number } | null;
  now: WeatherHour | null;
}

export interface CopModel {
  a: number;
  b: number;
  days: number;
}

export interface HouseModel {
  loss_w_per_k: number;
  gains_w: number;
  sun_m2: number | null;
  days: number;
  r2: number | null;
  spread_kwh: number | null;
  /** False: start value from the static balancing. */
  learned: boolean;
  lag_h: number | null;
  cop: CopModel | null;
}

export interface DayDemand {
  day: string;
  heat_kwh: number;
  electricity_kwh: number | null;
  outdoor_c: number | null;
}

export interface DayCycles {
  day: string;
  starts: number;
  run_minutes: number | null;
  pause_minutes: number | null;
  defrosts: number;
  outdoor_c: number | null;
}

export interface BandCycles {
  band: string;
  starts: number;
  run_minutes: number | null;
  pause_minutes: number | null;
  defrosts: number;
  run_hours: number;
}

export interface Defrost {
  at: string;
  minutes: number;
  peak_w: number;
  outdoor_c: number | null;
  humidity: number | null;
}

export interface CycleStats {
  today: DayCycles;
  days: DayCycles[];
  bands: BandCycles[];
  running: boolean | null;
  since: string | null;
  last_defrost: Defrost | null;
}

export interface BandRate {
  band: string;
  rate: number;
  defrosts: number;
  run_hours: number;
  learned: boolean;
}

export interface FrostState {
  active: boolean;
  below_since: string | null;
}

export interface Zone1Status {
  zone_id: string;
  would: 'off' | 'auto' | null;
  actual: string | null;
  reason: string;
  switches_today: number;
  checked_today: number;
  agreed_today: number;
}

export interface FlowStatus {
  zone_id: string;
  offset_k: number;
  hint: FlowHint;
  reason: string | null;
  bottleneck: string | null;
  max_valve: number | null;
  under: number;
}

export interface Change {
  at: string;
  text: string;
  reasons: string[];
}

export interface HemsStatus {
  mode: PlannerMode;
  available_modes: PlannerMode[];
  app_mode: OperatingMode;
  settings: HemsSettings;
  /** German: missing or outdated inputs. */
  problems: string[];
  /** Zone ID -> name. */
  labels: Record<string, string>;
  forecast: ForecastStatus;
  house: HouseModel | null;
  statistics_days: number;
  demand: DayDemand[];
  cycles: CycleStats;
  defrost_rates: BandRate[];
  frost: FrostState;
  zone1: Zone1Status | null;
  flow: FlowStatus[];
  next_changes: Change[];
  plan_created_at: string | null;
}

export interface ZoneHour {
  zone_id: string;
  shift_k: number;
  target_c: number | null;
  flow_c: number | null;
  block: boolean;
  night: boolean;
}

export interface RoomHour {
  entity_id: string;
  setpoint_c: number;
}

export interface PlanHour {
  start: string;
  outdoor_c: number | null;
  humidity: number | null;
  defrost: RiskLevel | null;
  heat_w: number | null;
  electricity_w: number | null;
  frost: boolean;
  zones: ZoneHour[];
  rooms: RoomHour[];
  reasons: string[];
}

export interface HourRecord {
  start: string;
  planned: PlanHour | null;
  outdoor_c: number | null;
  /** Zone ID -> mean setpoint in the hour. */
  setpoints: Record<string, number>;
  starts: number;
  defrosts: number;
  electricity_kwh: number | null;
}

export interface PlanView {
  created_at: string | null;
  hours: PlanHour[];
  history: HourRecord[];
  labels: Record<string, string>;
  /** Room entity -> name. */
  rooms: Record<string, string>;
}

export type SurfaceKind = 'radiator' | 'floor';
export type RatedBasis = '75/65' | '55/45' | '50/40';

export interface HeatingSurface {
  kind: SurfaceKind;
  rated_w: number | null;
  rated_basis: RatedBasis;
  flow_lph: number | null;
  presetting: string;
}

export interface ScheduleWindow {
  start: string;
  end: string;
  setpoint_c: number;
}

export interface RoomDetails {
  heat_load_w: number | null;
  design_c: number | null;
  surfaces: HeatingSurface[];
  trv_entity: string | null;
  valve_entity: string | null;
  humidity_entity: string | null;
  /** Optional window contact (binary_sensor); with it the contact alone decides (1.4.3). */
  window_entity: string | null;
  setpoint_c: number;
  schedule: ScheduleWindow[];
  comfort_down_k: number;
  comfort_up_k: number;
  night_loss_k: number | null;
  defrost_loss_k: number | null;
  throttle: boolean;
  emergency_c: number | null;
  stage: RoomStage;
  correction_limit_k: number;
  offset_k: number;
  deadband_k: number;
  min_interval_min: number;
  max_commands: number;
  /** The thermostat's name in Zigbee2MQTT when it differs from its device in Home Assistant. */
  zigbee_name: string | null;
}

export interface RoomLearned {
  tau_h: number | null;
  gains_k_per_h: number | null;
  heating_rate: number | null;
  startup_min: number | null;
  demand_valve: number | null;
  correction_k: number | null;
  overshoot_k: number | null;
  /** Value name -> learned (true) or start value / unknown (false). */
  learned: Record<string, boolean>;
  learning: boolean;
}

export interface RoomStatus {
  entity_id: string;
  name: string;
  zone_id: string | null;
  zone_label: string | null;
  measured_at_thermostat: boolean;
  details: RoomDetails;
  temperature_c: number | null;
  humidity: number | null;
  valve: number | null;
  trv_setpoint_c: number | null;
  setpoint_c: number;
  comfort_low_c: number;
  comfort_high_c: number;
  heat_loss_start_w_per_k: number | null;
  model: RoomLearned | null;
  cooling_now_k_per_h: number | null;
  /** Outdoor °C (as text) -> K/h at the setpoint. */
  cooling_table: Record<string, number>;
  minutes_to_lower: number | null;
  supply: Supply;
  window_open: boolean;
  window_since: string | null;
  /** What decides about the window (1.4.3). */
  window_source: 'contact' | 'rules';
  /** The fall of humidity (g/m³) that shows an open window now; null: the humidity does not count. */
  window_threshold_g: number | null;
  /** The signal of the last opening or closing, German. */
  window_reason: string | null;
}

export interface ZoneRoomsStatus {
  zone_id: string;
  label: string;
  limiting_room: string | null;
  limiting_minutes: number | null;
  flow: FlowStatus | null;
}

export interface RoomsView {
  rooms: RoomStatus[];
  zones: ZoneRoomsStatus[];
}

/** The room control (1.4.0): the switch for all rooms and each room's own stage; the lower one counts. */
export type RoomControlMode = 'off' | 'observe' | 'preview' | 'control';
export type RoomStage = 'observe' | 'preview' | 'control';
export type HeatSupply = 'heat_pump' | 'buffer' | 'none' | 'unknown';
export type TestState = 'armed' | 'running' | 'done' | 'aborted' | 'failed';
export type TrvModel = 'aqara' | 'comet' | 'other';
export type RoomMode = 'normal' | 'preheat' | 'window' | 'safety' | 'donor' | 'hold';

export interface ProbeStatus {
  flow_entity: string | null;
  return_entity: string | null;
  room_id: string | null;
  room_name: string | null;
  since: string | null;
  flow_c: number | null;
  return_c: number | null;
  flow_age_min: number | null;
  return_age_min: number | null;
  /** Longest silence between two reports since the app started. */
  longest_gap_min: number | null;
  resolution_k: number | null;
  /** A reading counts this long after its report. */
  valid_hours: number;
  /** The return was warmer than the flow with warm water at the open valve. */
  swapped: boolean;
}

export interface TestStatus {
  state: TestState;
  state_name: string;
  message: string;
  armed_at: string;
  started_at: string | null;
  ended_at: string | null;
  phase: string | null;
  setpoint_c: number | null;
  commands: number;
  steps: number;
  nights: number;
}

export interface CurveStep {
  setpoint_c: number;
  trv_c: number | null;
  room_c: number | null;
  offset_k: number | null;
  valve: number | null;
  rising: boolean;
  minutes: number;
  moved_after_min: number | null;
  flow_c: number | null;
  return_c: number | null;
  heat_w: number | null;
}

/** The test runs of a thermostat: how many, and the steps of the last one. */
export interface CurveStatus {
  runs: number;
  complete_runs: number;
  last_at: string | null;
  last_ending: string | null;
  steps: CurveStep[];
}
export type ControllerKind = 'unknown' | 'on_off' | 'p' | 'pi';
export interface MapCell {
  offset_k: number;
  rising: boolean;
  valve: number;
  count: number;
}
/** What the app learned of the thermostat's valve (1.4.2). */
export interface MapStatus {
  /** "zu" and "ganz offen" are known: the room may go to "Steuern". */
  ready: boolean;
  missing: string[];
  closed_k: number | null;
  full_k: number | null;
  full_valve: number | null;
  opening_k: number | null;
  kind: ControllerKind;
  kind_name: string;
  pi_per_h_per_k: number | null;
  reaction_min: number | null;
  speed: number | null;
  settle_min: number | null;
  smallest_step_k: number | null;
  points_test: number;
  points_daily: number;
  responses: number;
  last_point_at: string | null;
  cells: MapCell[];
  water: string[];
}
/** The reads of a W600's valve position. */
export interface ReadInfo {
  topic: string | null;
  found_name: string | null;
  last_read: string | null;
  today: number;
  limit: number;
  problem: string | null;
}

export interface RoomControlRoom {
  room_id: string;
  name: string;
  zone_id: string | null;
  stage: RoomStage;
  effective: RoomControlMode;
  trv_entity: string | null;
  model: TrvModel | null;
  model_name: string | null;
  at_thermostat: boolean;
  trv_setpoint_c: number | null;
  trv_c: number | null;
  valve: number | null;
  target_c: number | null;
  setpoint_c: number | null;
  mode: RoomMode | null;
  mode_name: string | null;
  reason_name: string | null;
  note: string;
  correction_k: number;
  offset_k: number;
  commands_today: number;
  commands_limit: number;
  test_commands_today: number;
  preview_commands_today: number;
  movements_today: number;
  pending_c: number | null;
  last_sent: string | null;
  paused_until: string | null;
  warnings: string[];
  /** Other Zigbee2MQTT entities of the thermostat by name, e.g. "child_lock". */
  side: Record<string, string>;
  test: TestStatus | null;
  curve: CurveStatus;
  valve_map: MapStatus;
  /** Null for thermostats that report their valve themselves. */
  read: ReadInfo | null;
  life_sign_at: string | null;
  last_seen: boolean;
  valve_at: string | null;
  recommendation: string | null;
  donor: boolean;
  cold: boolean;
  can_control: boolean;
  control_blocked: string | null;
}

export interface ZoneControl {
  zone_id: string;
  label: string;
  supply: HeatSupply;
  supply_name: string;
  remaining_min: number | null;
  buffer_c: number | null;
  redistribution: string;
  cold_room: string | null;
  donors: string[];
  flow_hint: FlowHint | null;
  flow_text: string | null;
}

export interface RoomControlStatus {
  mode: RoomControlMode;
  app_mode: OperatingMode;
  lifesign_entity: string;
  lifesign_at: string | null;
  test_start: string;
  test_latest_start: string;
  test_end: string;
  mqtt_base_topic: string;
  probe: ProbeStatus;
  rooms: RoomControlRoom[];
  zones: ZoneControl[];
  problems: string[];
}

/** The page "Räume": the energy management's view of the rooms and the room control. */
export interface RoomsPage extends RoomsView {
  control: RoomControlStatus | null;
}
