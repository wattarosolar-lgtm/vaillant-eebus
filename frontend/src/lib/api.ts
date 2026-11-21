// HTTP access to the backend. All paths are relative because Home Assistant
// ingress serves the app below a token path.

import type {
  ApiState,
  AppSettings,
  Connection,
  ConnectionStatus,
  EntityChoice,
  EntityKind,
  ExportGroup,
  HemsSettings,
  HemsStatus,
  LogFilter,
  LogPage,
  MyVaillantEntities,
  OperatingMode,
  PairingStatus,
  PlannerMode,
  PlanView,
  PowerHistory,
  Room,
  RoomDetails,
  RoomsView,
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

export async function getConnection(): Promise<ConnectionStatus> {
  return (await send('api/connection')).json() as Promise<ConnectionStatus>;
}

/** Saves the connection and the myVAILLANT entities; a new connection restarts the app (restarting). */
export async function saveConnection(connection: Connection, myvaillant: MyVaillantEntities): Promise<ConnectionStatus> {
  const response = await send('api/connection', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ connection, myvaillant }),
  });
  return response.json() as Promise<ConnectionStatus>;
}

/** The myVAILLANT integration's entities found in Home Assistant, as a suggestion. */
export async function discoverMyVaillant(): Promise<MyVaillantEntities> {
  return (await send('api/myvaillant/discover')).json() as Promise<MyVaillantEntities>;
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

function zonePath(zoneId: string, what: 'setpoint' | 'mode' | 'override/end'): string {
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

/** myVAILLANT: ends the zone's temporary override (quick veto). */
export async function endZoneOverride(zoneId: string, shown: OperatingMode): Promise<WriteResult> {
  return post<WriteResult>(zonePath(zoneId, 'override/end'), { operating_mode: shown });
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

/** Home Assistant entities to choose from: rooms, heat of the day and the planner's inputs. */
export async function getEntities(kind: EntityKind, query: string): Promise<EntityChoice[]> {
  const params = new URLSearchParams({ kind, q: query });
  return (await send(`api/ha/entities?${params.toString()}`)).json() as Promise<EntityChoice[]>;
}

/** Home Assistant's home location, to fill in the coordinates for Open-Meteo. */
export async function getLocation(): Promise<{ latitude: number; longitude: number }> {
  return (await send('api/ha/location')).json() as Promise<{ latitude: number; longitude: number }>;
}

export async function getHems(): Promise<HemsStatus> {
  return (await send('api/hems')).json() as Promise<HemsStatus>;
}

/** Saves everything but the stage, which setHemsMode switches. */
export async function saveHemsSettings(settings: Omit<HemsSettings, 'mode'>): Promise<HemsStatus> {
  const response = await send('api/hems/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  return response.json() as Promise<HemsStatus>;
}

export async function setHemsMode(mode: PlannerMode): Promise<HemsStatus> {
  return post<HemsStatus>('api/hems/mode', { mode });
}

/** The plan for 48 hours and the look-back on the last 24. */
export async function getPlan(): Promise<PlanView> {
  return (await send('api/hems/plan')).json() as Promise<PlanView>;
}

export async function getRooms(): Promise<RoomsView> {
  return (await send('api/rooms')).json() as Promise<RoomsView>;
}

/** Saves what the user entered for one room. */
export async function saveRoom(entityId: string, details: RoomDetails): Promise<RoomsView> {
  const response = await send(`api/rooms/${encodeURIComponent(entityId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(details),
  });
  return response.json() as Promise<RoomsView>;
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
export async function downloadPlan(): Promise<void> {
  await download('api/hems/plan/export', 'vaillant-eebus-plan.csv');
}

export async function downloadLog(filter: LogFilter): Promise<void> {
  const query = logParams(filter).toString();
  await download(query ? `api/log/export?${query}` : 'api/log/export', 'vaillant-eebus-protokoll.csv');
}

export async function getPowerHistory(): Promise<PowerHistory> {
  return (await send('api/history/power')).json() as Promise<PowerHistory>;
}

/** Delays before opening a new stream after the last one failed for good; the last one repeats. */
const STREAM_RETRY_MS = [1000, 2000, 5000, 10000, 15000];
/** The backend sends a ping every 15 s; a stream without any message for this long hangs. */
const STREAM_SILENCE_MS = 45000;
/** Back on the page after this long without a message: do not wait for the silence limit. */
const STREAM_STALE_MS = 20000;

/**
 * Follows the backend's server-sent events and keeps following them.
 *
 * The browser reconnects by itself only after a network error. An error answer
 * ends its EventSource for good: Home Assistant's ingress answers 502 while the
 * app restarts (update, crash) and 401 once its session ran out (after sleep).
 * Then a new stream is opened after STREAM_RETRY_MS. A stream that stays silent
 * is replaced too: after sleep or a network change the old connection can hang
 * without any error. onOpenChange reports whether the stream is up. Returns a
 * function that closes the stream for good.
 */
export function subscribeState(onState: (state: ApiState) => void, onOpenChange: (open: boolean) => void): () => void {
  let source: EventSource | null = null;
  let failures = 0;
  let lastMessage = Date.now();
  let retryTimer: number | undefined;
  let silenceTimer: number | undefined;

  const alive = (): void => {
    lastMessage = Date.now();
    window.clearTimeout(silenceTimer);
    silenceTimer = window.setTimeout(retry, STREAM_SILENCE_MS);
  };

  function open(): void {
    window.clearTimeout(retryTimer);
    source?.close();
    const current = new EventSource('api/events');
    source = current;
    alive();
    current.addEventListener('open', () => {
      if (current === source) {
        failures = 0;
        alive();
        onOpenChange(true);
      }
    });
    current.addEventListener('state', (event: MessageEvent<string>) => {
      if (current === source) {
        alive();
        onState(JSON.parse(event.data) as ApiState);
      }
    });
    current.addEventListener('ping', () => {
      if (current === source) {
        alive();
      }
    });
    current.addEventListener('error', () => {
      if (current !== source) {
        return;
      }
      onOpenChange(false);
      // CONNECTING: the browser tries again by itself, the silence limit covers it.
      if (current.readyState === EventSource.CLOSED) {
        retry();
      }
    });
  }

  /** Drops the current stream and opens a new one after the next delay. */
  function retry(): void {
    source?.close();
    window.clearTimeout(silenceTimer);
    window.clearTimeout(retryTimer);
    onOpenChange(false);
    const delay = STREAM_RETRY_MS[Math.min(failures, STREAM_RETRY_MS.length - 1)];
    failures += 1;
    retryTimer = window.setTimeout(open, delay);
  }

  /** Back on the page or online again: reconnect at once instead of waiting for a retry or the silence limit. */
  const wake = (): void => {
    const stale = source?.readyState === EventSource.CLOSED || Date.now() - lastMessage > STREAM_STALE_MS;
    if (document.visibilityState === 'visible' && stale) {
      failures = 0;
      open();
    }
  };

  open();
  document.addEventListener('visibilitychange', wake);
  window.addEventListener('online', wake);
  return () => {
    document.removeEventListener('visibilitychange', wake);
    window.removeEventListener('online', wake);
    window.clearTimeout(retryTimer);
    window.clearTimeout(silenceTimer);
    source?.close();
    source = null;
  };
}
