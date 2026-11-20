// German number formatting only; the values themselves come from the backend.

import type { EnergyToday, Reading } from './types';

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

export function energyToday(energy: EnergyToday | null | undefined): string | null {
  return energy ? `${formatNumber(energy.kwh, 2)} kWh${energy.incomplete ? ' (lückenhaft)' : ''}` : null;
}

export function timeOfDay(iso: string | null): string | null {
  if (!iso) {
    return null;
  }
  return new Date(iso).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

const clockFormat = new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' });
const weekdayFormat = new Intl.DateTimeFormat('de-DE', { weekday: 'short' });

/** "14:00", with the weekday in front when it is not today ("Fr 03:00"). */
export function moment(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  const today = at.toDateString() === now.toDateString();
  return `${today ? '' : `${weekdayFormat.format(at)} `}${clockFormat.format(at)}`;
}

/** A shift in K with its sign: "+0,5", "−1", "±0". */
export function signed(value: number, digits = 1): string {
  if (value === 0) {
    return '±0';
  }
  const text = formatNumber(Math.abs(value), Number.isInteger(value) ? 0 : digits);
  return `${value > 0 ? '+' : '−'}${text}`;
}

/** A number, or a dash for none. */
export function maybe(value: number | null | undefined, digits: number, unit = ''): string {
  return value === null || value === undefined ? '–' : `${formatNumber(value, digits)}${unit ? ` ${unit}` : ''}`;
}

/** Minutes as "45 min" or "2 h 10 min". */
export function duration(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) {
    return '–';
  }
  const rounded = Math.round(minutes);
  if (rounded < 60) {
    return `${rounded} min`;
  }
  const hours = Math.floor(rounded / 60);
  const rest = rounded % 60;
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}
