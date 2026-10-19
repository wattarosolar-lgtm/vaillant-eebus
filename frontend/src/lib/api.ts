// HTTP access to the backend. All paths are relative because Home Assistant
// ingress serves the app below a token path.

import type { ApiState, LogCategory, LogPage, OperatingMode, PairingStatus, WriteResult } from './types';

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const BACKEND_UNREACHABLE = 'Die App antwortet gerade nicht. Bitte kurz warten oder die App neu starten.';

async function errorFrom(response: Response): Promise<ApiError> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
      return new ApiError(response.status, body.detail);
    }
  } catch {
    // Not a JSON error body; fall through to the generic message.
  }
  return new ApiError(response.status, `Unerwarteter Fehler der App (HTTP ${response.status}).`);
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(path, { ...init, cache: 'no-store' });
  } catch {
    throw new ApiError(0, BACKEND_UNREACHABLE);
  }
  if (!response.ok) {
    throw await errorFrom(response);
  }
  return response;
}

export async function getPairing(): Promise<PairingStatus> {
  return (await send('api/pairing')).json() as Promise<PairingStatus>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await send(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return response.json() as Promise<T>;
}

export async function registerGateway(ski: string): Promise<PairingStatus> {
  return post<PairingStatus>('api/pairing/register', { ski });
}

/** Live needs confirm: true; the backend refuses it otherwise. */
export async function setOperatingMode(mode: OperatingMode, confirm: boolean): Promise<{ mode: OperatingMode }> {
  return post<{ mode: OperatingMode }>('api/mode', { mode, confirm });
}

function zonePath(zoneId: string, what: 'setpoint' | 'mode'): string {
  return `api/zones/${encodeURIComponent(zoneId)}/${what}`;
}

export async function setZoneSetpoint(zoneId: string, value: number): Promise<WriteResult> {
  return post<WriteResult>(zonePath(zoneId, 'setpoint'), { value });
}

export async function setZoneMode(zoneId: string, mode: string): Promise<WriteResult> {
  return post<WriteResult>(zonePath(zoneId, 'mode'), { mode });
}

export async function setHotWaterSetpoint(value: number): Promise<WriteResult> {
  return post<WriteResult>('api/dhw/setpoint', { value });
}

export async function setHotWaterMode(mode: string): Promise<WriteResult> {
  return post<WriteResult>('api/dhw/mode', { mode });
}

export async function setHotWaterBoost(active: boolean): Promise<WriteResult> {
  return post<WriteResult>('api/dhw/boost', { active });
}

/** Protocol entries, newest first; before continues after the last entry of a page. */
export async function getLog(category: LogCategory | null, before: number | null): Promise<LogPage> {
  const params = new URLSearchParams();
  if (category) {
    params.set('category', category);
  }
  if (before !== null) {
    params.set('before', String(before));
  }
  const query = params.toString();
  return (await send(query ? `api/log?${query}` : 'api/log')).json() as Promise<LogPage>;
}

function filenameFrom(disposition: string | null): string {
  const match = disposition?.match(/filename="([^"]+)"/);
  return match?.[1] ?? 'vaillant-eebus-diagnose.json';
}

/** Fetches the diagnostic dump and hands it to the browser as a file download. */
export async function downloadDump(): Promise<void> {
  const response = await send('api/diagnostics/dump');
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filenameFrom(response.headers.get('Content-Disposition'));
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function getState(): Promise<ApiState> {
  return (await send('api/state')).json() as Promise<ApiState>;
}

/**
 * Follows the backend's server-sent events. The browser reconnects by itself
 * after an interruption; onOpenChange reports whether the stream is up.
 * Returns a function that closes the stream.
 */
export function subscribeState(onState: (state: ApiState) => void, onOpenChange: (open: boolean) => void): () => void {
  const source = new EventSource('api/events');
  source.addEventListener('state', (event: MessageEvent<string>) => {
    onState(JSON.parse(event.data) as ApiState);
  });
  source.addEventListener('open', () => onOpenChange(true));
  source.addEventListener('error', () => onOpenChange(false));
  return () => source.close();
}
