import type {
  ConnectionState,
  ExportGroup,
  Gateway,
  HistoryGap,
  LogCategory,
  PlannerMode,
  TariffKind,
  ValueStatus,
} from './types';

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
 * In auto mode the controller follows its time program, which the gateway does
 * not report; the setpoint it reports is the one for "on", and writing it has
 * no effect in auto (field test of 0.5.1). myVAILLANT starts a boost instead,
 * so the app shows the value but points there for changes.
 */
export const AUTO_MODE_SETPOINT_LOCKED =
  'Im Auto-Betrieb gilt das Zeitprogramm. Der angezeigte Wert gilt nur in „Ein“; für einen Boost myVAILLANT oder Home Assistant nutzen.';

/** Shown in demo mode only; live mode shows the orange switch, no banner. */
export const DEMO_BANNER =
  'Demo-Betrieb: Die App liest nur Werte. Änderungen werden angezeigt, aber nicht an die Wärmepumpe gesendet.';

export const LIVE_CONFIRMATION =
  'Live-Betrieb einschalten? Danach sendet die App Änderungen wirklich an die Wärmepumpe.';

export const LOG_CATEGORY_LABELS: Record<LogCategory, string> = {
  write: 'Schreiben',
  device: 'Gerät',
  connection: 'Verbindung',
  mode: 'Modus',
  settings: 'Einstellungen',
  hems: 'Planer',
};

/** Groups of values reported to Home Assistant, in the order of the settings page. */
export const EXPORT_GROUPS: { group: ExportGroup; label: string; hint: string }[] = [
  { group: 'status', label: 'Status', hint: 'Verbunden, Heartbeat, Betriebszustand, Verdichter läuft' },
  { group: 'zones', label: 'Zonen', hint: 'Raumtemperatur (Mittelwert), Solltemperatur und Betriebsart je Zone' },
  { group: 'hot_water', label: 'Warmwasser', hint: 'Speichertemperatur, Solltemperatur, Betriebsart, Einmalladung' },
  { group: 'temperatures', label: 'Temperaturen', hint: 'Außentemperatur und weitere, soweit die Anlage sie meldet' },
  { group: 'energy', label: 'Leistung und Energie', hint: 'Leistung, Verdichterleistung, Energie heute (berechnet)' },
  { group: 'power_limits', label: 'Leistungsdeckel', hint: 'Grenze, aktiv, Failsafe-Grenze und -Dauer' },
  { group: 'compressor', label: 'Verdichter-Flexibilität', hint: 'Zustand eines angebotenen Verdichterlaufs' },
  { group: 'hems', label: 'Energiemanagement', hint: 'Stufe des Planers, nächste Aktion, Plan, Strombedarf morgen' },
];

export const PLANNER_MODE_LABELS: Record<PlannerMode, string> = {
  off: 'Aus',
  preview: 'Vorschau',
  control: 'Steuern',
};

export const PLANNER_MODE_HINTS: Record<PlannerMode, string> = {
  off: 'Der Planer plant nicht und ändert nichts.',
  preview: 'Der Planer plant und zeigt, was er senden würde. Er sendet nichts, auch im Live-Betrieb.',
  control:
    'Der Planer setzt den Plan um: Er verschiebt Solltemperaturen und startet die Warmwasser-Einmalladung. Im Demo-Betrieb der App sendet er trotzdem nichts.',
};

export const CONTROL_CONFIRMATION =
  'Steuern einschalten? Der Planer ändert dann Solltemperaturen der Wärmepumpe und kehrt danach jeweils zum Grundwert zurück.';

export const TARIFF_LABELS: Record<TariffKind, string> = {
  fixed: 'Festpreis',
  dynamic: 'Dynamisch (Börsenpreis)',
  fixed_14a: 'Festpreis mit §14a Modul 3',
};

/** Shown instead of the power chart. */
export const HISTORY_GAPS: Record<HistoryGap, string> = {
  no_home_assistant: 'Den Verlauf liefert Home Assistant; außerhalb davon gibt es ihn nicht.',
  not_exported:
    'Für den Verlauf unter Einstellungen → Export nach Home Assistant die Gruppe „Leistung und Energie“ einschalten.',
  unreachable: 'Home Assistant ist gerade nicht erreichbar; der Verlauf fehlt deshalb.',
};

export const VALUE_STATUS_LABELS: Record<ValueStatus, string> = {
  ok: '',
  stale: 'veraltet',
  unavailable: 'nicht verfügbar',
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

function deviceTypeName(deviceType: string): string {
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

function categoryNames(categories: number[]): string {
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

const PROJECT_URL = 'https://github.com/wattarosolar-lgtm/vaillant-eebus';

/** Links on the settings page ("Über die App"). */
export const PROJECT_LINKS: { label: string; href: string }[] = [
  { label: 'Anleitung', href: `${PROJECT_URL}/blob/main/vaillant-eebus/DOCS.md` },
  { label: 'Änderungen', href: `${PROJECT_URL}/blob/main/vaillant-eebus/CHANGELOG.md` },
  { label: 'Fehler melden', href: `${PROJECT_URL}/issues` },
];

/** SKI in groups of four characters, easier to compare by eye. */
export function groupedSki(ski: string): string {
  return ski.match(/.{1,4}/g)?.join(' ') ?? ski;
}

export function skiEnding(ski: string): string {
  return ski.slice(-6);
}
