import type { ConnectionState, Gateway } from './types';

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

const DEVICE_TYPES: Record<string, string> = {
  EnergyManagementSystem: 'Energiemanager',
  HeatGenerationSystem: 'Wärmeerzeuger',
  HeatPumpAppliance: 'Wärmepumpe',
  Generic: 'EEBUS-Gerät',
};

export function deviceTypeName(deviceType: string): string {
  return DEVICE_TYPES[deviceType] ?? deviceType;
}

/** Secondary line for a gateway: only the details the device announced. */
export function gatewayDetails(gateway: Gateway): string {
  return [
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

export function gatewayName(gateway: Gateway): string {
  const name = [gateway.brand, gateway.model].filter(Boolean).join(' ');
  return name || 'Unbekanntes EEBUS-Gerät';
}
