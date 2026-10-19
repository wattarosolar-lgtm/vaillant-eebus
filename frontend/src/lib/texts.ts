import type { ConnectionState, Gateway } from './types';

export type Tone = 'ok' | 'warn' | 'error' | 'neutral';

export const STATE_LABELS: Record<ConnectionState, { label: string; tone: Tone }> = {
  not_paired: { label: 'Nicht gekoppelt', tone: 'neutral' },
  bridge_unavailable: { label: 'Bridge nicht erreichbar', tone: 'error' },
  disconnected: { label: 'Getrennt', tone: 'warn' },
  connected: { label: 'Verbunden', tone: 'ok' },
  trust_removed: { label: 'Vertrauen entzogen', tone: 'error' },
  failed: { label: 'Verbindung gestört', tone: 'error' },
};

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
