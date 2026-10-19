// HTTP access to the backend. All paths are relative because Home Assistant
// ingress serves the app below a token path.

import type { PairingStatus } from './types';

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

export async function registerGateway(ski: string): Promise<PairingStatus> {
  const response = await send('api/pairing/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ski }),
  });
  return response.json() as Promise<PairingStatus>;
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
