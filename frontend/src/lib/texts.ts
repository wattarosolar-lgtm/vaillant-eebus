import type { ConnectionState, Gateway, LogCategory } from './types';

export type Tone = 'ok' | 'warn' | 'error' | 'neutral';

export const STATE_LABELS: Record<ConnectionState, { label: string; tone: Tone }> = {
  not_paired: { label: 'Nicht gekoppelt', tone: 'neutral' },
  bridge_unavailable: { label: 'Bridge nicht erreichbar', tone: 'error' },
  ship_port_in_use: { label: 'EEBUS-Port belegt', tone: 'error' },
  foreign_bridge: { label: 'Fremde Bridge', tone: 'error' },
  disconnected: { label: 'Getrennt', tone: 'warn' },
  connected: { label: 'Verbunden', tone: 'ok' },
  trust_removed: { label: 'Vertrauen entzogen', tone: 'error' },
  failed: { label: 'Verbindung gestört', tone: 'error' },
};

const OTHER_EEBUS_APP =
  'Vermutlich läuft noch eine andere EEBUS-App oder das Add-on „EEBUS Bridge“. Bitte unter Einstellungen → Apps stoppen und den Autostart ausschalten.';

/** Explanations for states in which this app's bridge cannot be used. */
export const BRIDGE_PROBLEMS: Partial<Record<ConnectionState, string>> = {
  bridge_unavailable:
    'Die EEBUS-Bridge der App ist gerade nicht erreichbar. Sie wird automatisch neu gestartet; hält der Zustand an, bitte das Log der App prüfen.',
  ship_port_in_use: `Die EEBUS-Bridge der App kann nicht starten, weil der EEBUS-Port 4712 schon belegt ist. ${OTHER_EEBUS_APP} Die Bridge startet danach von selbst.`,
  foreign_bridge: `Unter der Adresse dieser App antwortet eine fremde EEBUS-Bridge. ${OTHER_EEBUS_APP} Danach diese App neu starten.`,
};

/** Operation modes in the order of the design's segment buttons. */
export const MODE_ORDER = ['off', 'auto', 'on', 'eco'];

export const MODE_LABELS: Record<string, string> = {
  off: 'Aus',
  auto: 'Auto',
  on: 'Ein',
  eco: 'Eco',
};

/** SPINE operating states (deviceDiagnosisStateData). */
const OPERATING_STATES: Record<string, string> = {
  normalOperation: 'Normalbetrieb',
  standby: 'Bereitschaft',
  failure: 'Störung',
  serviceNeeded: 'Wartung nötig',
  overrideDetected: 'Übersteuert',
  inAlarm: 'Alarm',
  notReachable: 'Nicht erreichbar',
  finished: 'Beendet',
  temporarilyNotReady: 'Vorübergehend nicht bereit',
  off: 'Aus',
};

export function operatingStateName(state: string): string {
  return OPERATING_STATES[state] ?? state;
}

/**
 * In auto mode the controller follows its time program. The gateway reports
 * only one setpoint per zone, not the time program's current temperature
 * (field finding: myVAILLANT showed 21,5 °C while the gateway reported 23 °C).
 */
export const AUTO_MODE_SETPOINT_NOTE =
  'Im Auto-Betrieb regelt der Regler nach seinem Zeitprogramm; dessen aktuelle Solltemperatur meldet das Gateway nicht.';

export const OPERATING_MODE_BANNERS = {
  demo: 'Demo-Betrieb: Die App liest nur Werte. Änderungen werden angezeigt, aber nicht an die Wärmepumpe gesendet.',
  live: 'Live-Betrieb: Änderungen werden an die Wärmepumpe gesendet.',
} as const;

export const LIVE_CONFIRMATION =
  'Live-Betrieb einschalten? Danach sendet die App Änderungen wirklich an die Wärmepumpe.';

export const LOG_CATEGORY_LABELS: Record<LogCategory, string> = {
  write: 'Schreiben',
  device: 'Gerät',
  connection: 'Verbindung',
  mode: 'Modus',
};

export const BOOST_LABELS: Record<string, string> = {
  inactive: 'Aus',
  active: 'Angefordert',
  running: 'Läuft',
  finished: 'Beendet',
};

const DEVICE_TYPES: Record<string, string> = {
  EnergyManagementSystem: 'Energiemanager',
  HeatGenerationSystem: 'Wärmeerzeuger',
  HeatPumpAppliance: 'Wärmepumpe',
  Generic: 'EEBUS-Gerät',
};

export function deviceTypeName(deviceType: string): string {
  return DEVICE_TYPES[deviceType] ?? deviceType;
}

/** SHIP device categories (SHIP Requirements for Installation Process). */
const CATEGORIES: Record<number, string> = {
  1: 'Netzanschlusspunkt',
  2: 'Energiemanager',
  3: 'E-Mobilität',
  4: 'Heizung/Klima',
  5: 'Wechselrichter',
  6: 'Haushaltsgerät',
  7: 'Messgerät',
};

export function categoryNames(categories: number[]): string {
  return categories.map((category) => CATEGORIES[category] ?? `Kategorie ${category}`).join(', ');
}

/** Best available name: brand and model, else mDNS instance name, SHIP id or category. */
export function gatewayName(gateway: Gateway): string {
  const model = [gateway.brand, gateway.model].filter(Boolean).join(' ');
  return model || gateway.name || gateway.ship_id || categoryNames(gateway.categories) || 'EEBUS-Gerät';
}

/** Secondary line for a gateway: only the details the device announced. */
export function gatewayDetails(gateway: Gateway): string {
  return [
    gateway.categories.length > 0 && categoryNames(gateway.categories),
    gateway.device_type && `Gerätetyp ${deviceTypeName(gateway.device_type)}`,
    gateway.serial && `Seriennummer ${gateway.serial}`,
    gateway.host && `Adresse ${gateway.host}`,
  ]
    .filter(Boolean)
    .join(' · ');
}

/** SKI in groups of four characters, easier to compare by eye. */
export function groupedSki(ski: string): string {
  return ski.match(/.{1,4}/g)?.join(' ') ?? ski;
}

export function skiEnding(ski: string): string {
  return ski.slice(-6);
}
