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
  | 'failed';

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

export type LogCategory = 'mode' | 'write' | 'connection' | 'device' | 'settings' | 'hems';

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

/** What Home Assistant entities are offered for: rooms, heat, and the planner's inputs. */
export type EntityKind = 'temperature' | 'energy' | 'power' | 'battery' | 'price' | 'pv_forecast' | 'weather';

// --- energy management (hems/) ---------------------------------------------------

/** Aus, Vorschau (plans, sends nothing), Steuern (through the write gate). */
export type PlannerMode = 'off' | 'preview' | 'control';
export type TariffKind = 'fixed' | 'dynamic' | 'fixed_14a';
export type WeatherSource = 'home_assistant' | 'open_meteo';

/** Local time of day "HH:MM"; an end before the start crosses midnight. */
export interface TimeWindow {
  start: string;
  end: string;
}

export interface Tariff {
  kind: TariffKind;
  /** ct/kWh; with §14a the price in standard time. */
  price_ct: number | null;
  price_entity: string | null;
  surcharge_ct: number;
  vat_percent: number;
  high_price_ct: number | null;
  low_price_ct: number | null;
  high_windows: TimeWindow[];
  low_windows: TimeWindow[];
  feed_in_ct: number | null;
}

export interface ZoneLimits {
  zone_id: string;
  up_k: number;
  down_k: number;
}

/** The settings as the backend stores them (hems_to_json). */
export interface HemsSettings {
  mode: PlannerMode;
  tariff: Tariff;
  pv_forecast_today: string | null;
  pv_forecast_tomorrow: string | null;
  house_power: string | null;
  battery_soc: string | null;
  battery_capacity_kwh: number | null;
  battery_charge_kw: number | null;
  battery_min_soc: number;
  weather_source: WeatherSource;
  weather_entity: string | null;
  latitude: number | null;
  longitude: number | null;
  zones: ZoneLimits[];
  dhw_max_c: number;
  heating_limit_c: number;
}

export interface PlanSlot {
  start: string;
  /** EUR/kWh from the grid. */
  price: number | null;
  pv_w: number | null;
  base_w: number | null;
  /** What would go to the grid otherwise, W. */
  surplus_w: number | null;
  battery_soc: number | null;
  outdoor_c: number | null;
}

export type BlockAction = 'charge' | 'raise' | 'lower';

export interface PlanBlock {
  /** "dhw" or the zone's ID. */
  control: string;
  action: BlockAction;
  start: string;
  end: string;
  shift_k: number | null;
  target_c: number | null;
  reason: string;
}

export interface Plan {
  created_at: string;
  slots: PlanSlot[];
  blocks: PlanBlock[];
  /** Per control: why nothing is planned. */
  notes: Record<string, string>;
}

export interface NextAction {
  at: string;
  text: string;
  reason: string | null;
}

export interface Demand {
  kwh: number;
  spread_kwh: number;
  days: number;
  outdoor_c: number;
}

export interface HemsStatus {
  mode: PlannerMode;
  app_mode: OperatingMode;
  settings: HemsSettings;
  plan: Plan | null;
  problems: string[];
  next_action: NextAction | null;
  labels: Record<string, string>;
  bases: Record<string, number>;
  shifted: Record<string, number>;
  /** EUR/kWh. */
  feed_in: number | null;
  demand_tomorrow: Demand | null;
  learned_days: number;
  needed_days: number;
}
