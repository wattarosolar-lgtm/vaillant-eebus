// German number formatting only; the values themselves come from the backend.

import type { Reading } from './types';

const formatters = new Map<number, Intl.NumberFormat>();

export function formatNumber(value: number, digits: number): string {
  let formatter = formatters.get(digits);
  if (!formatter) {
    formatter = new Intl.NumberFormat('de-DE', { minimumFractionDigits: digits, maximumFractionDigits: digits });
    formatters.set(digits, formatter);
  }
  return formatter.format(value);
}

/** Digits needed to show values on the given step, e.g. 0.5 → 1. */
export function digitsForStep(step: number): number {
  return step > 0 && step < 1 ? 1 : 0;
}

export function temperature(reading: Reading | null | undefined): string | null {
  return reading ? formatNumber(reading.value, 1) : null;
}

/** Electrical power in kW, as on the design's power card. */
export function kilowatts(reading: Reading | null | undefined): string | null {
  return reading ? formatNumber(reading.value / 1000, 2) : null;
}

export function watts(reading: Reading | null | undefined): string | null {
  return reading ? `${formatNumber(reading.value, 0)} W` : null;
}

export function kilowattHours(reading: Reading | null | undefined): string | null {
  return reading ? `${formatNumber(reading.value, 1)} kWh` : null;
}

export function timeOfDay(iso: string | null): string | null {
  if (!iso) {
    return null;
  }
  return new Date(iso).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
