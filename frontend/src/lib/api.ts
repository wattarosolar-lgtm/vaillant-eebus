// HTTP access to the backend. All paths are relative because Home Assistant
// ingress serves the app below a token path.

import type {
  ApiState,
  AppSettings,
  EntityChoice,
  ExportGroup,
  LogFilter,
  LogPage,
  OperatingMode,
  PairingStatus,
  PowerHistory,
  Room,
  WriteResult,
} from './types';

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

// Every write names the operating mode the user saw when making the change
// (shown); the backend refuses it if the mode changed since.

export async function setZoneSetpoint(zoneId: string, value: number, shown: OperatingMode): Promise<WriteResult> {
  return post<WriteResult>(zonePath(zoneId, 'setpoint'), { value, operating_mode: shown });
}

export async function setZoneMode(zoneId: string, mode: string, shown: OperatingMode): Promise<WriteResult> {
  return post<WriteResult>(zonePath(zoneId, 'mode'), { mode, operating_mode: shown });
}

export async function setHotWaterSetpoint(value: number, shown: OperatingMode): Promise<WriteResult> {
  return post<WriteResult>('api/dhw/setpoint', { value, operating_mode: shown });
}

export async function setHotWaterMode(mode: string, shown: OperatingMode): Promise<WriteResult> {
  return post<WriteResult>('api/dhw/mode', { mode, operating_mode: shown });
}

export async function setHotWaterBoost(active: boolean, shown: OperatingMode): Promise<WriteResult> {
  return post<WriteResult>('api/dhw/boost', { active, operating_mode: shown });
}

export async function getSettings(): Promise<AppSettings> {
  return (await send('api/settings')).json() as Promise<AppSettings>;
}

export async function saveSettings(change: {
  rooms: Room[];
  heat_energy_entities: string[];
  export: ExportGroup[];
}): Promise<AppSettings> {
  const response = await send('api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(change),
  });
  return response.json() as Promise<AppSettings>;
}

/** Home Assistant sensors to choose from: temperatures for rooms, energy for the heat of the day. */
export async function getEntities(kind: 'temperature' | 'energy', query: string): Promise<EntityChoice[]> {
  const params = new URLSearchParams({ kind, q: query });
  return (await send(`api/ha/entities?${params.toString()}`)).json() as Promise<EntityChoice[]>;
}

function logParams(filter: LogFilter): URLSearchParams {
  const params = new URLSearchParams();
  if (filter.category) {
    params.set('category', filter.category);
  }
  if (filter.problems) {
    params.set('problems', 'true');
  }
  return params;
}

/**
 * Protocol entries, newest first. before continues after the last entry of a
 * page, after asks for entries newer than the newest one shown, until (a local
 * day, YYYY-MM-DD) starts at the end of that day.
 */
export async function getLog(
  filter: LogFilter,
  position: { before?: number; after?: number; until?: string } = {},
): Promise<LogPage> {
  const params = logParams(filter);
  for (const [name, value] of Object.entries(position)) {
    if (value !== undefined) {
      params.set(name, String(value));
    }
  }
  const query = params.toString();
  return (await send(query ? `api/log?${query}` : 'api/log')).json() as Promise<LogPage>;
}

function filenameFrom(disposition: string | null, fallback: string): string {
  const match = disposition?.match(/filename="([^"]+)"/);
  return match?.[1] ?? fallback;
}

/** Fetches a file from the backend and hands it to the browser as a download. */
async function download(path: string, fallbackName: string): Promise<void> {
  const response = await send(path);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filenameFrom(response.headers.get('Content-Disposition'), fallbackName);
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function downloadDump(): Promise<void> {
  await download('api/diagnostics/dump', 'vaillant-eebus-diagnose.json');
}

/** The protocol as a CSV file, with the same filter as the list. */
export async function downloadLog(filter: LogFilter): Promise<void> {
  const query = logParams(filter).toString();
  await download(query ? `api/log/export?${query}` : 'api/log/export', 'vaillant-eebus-protokoll.csv');
}

export async function getPowerHistory(): Promise<PowerHistory> {
  return (await send('api/history/power')).json() as Promise<PowerHistory>;
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
