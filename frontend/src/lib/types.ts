// Mirrors the backend's dataclasses (pairing.py, state.py, write_gate.py, log_store.py, api.py).

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

export interface ApiState {
  connection: ConnectionState;
  mode: OperatingMode;
  pending: PendingWrite[];
  heat_pump: HeatPumpState | null;
}

export type LogCategory = 'mode' | 'write' | 'connection' | 'device';

export interface LogEntry {
  id: number;
  time: string;
  category: LogCategory;
  message: string;
}

export interface LogPage {
  entries: LogEntry[];
  more: boolean;
}
