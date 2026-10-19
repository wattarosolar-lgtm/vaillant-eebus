// What a control shows after the user changed it: the demo preview, a note
// from the backend or an error. The values themselves always come from the
// device; a preview only stands for a few seconds (spec 9).

import { ApiError } from './api';
import type { WriteResult } from './types';

/** How long a demo preview and a note stay visible. */
export const PREVIEW_MS = 5000;

export class WriteFeedback<T> {
  /** Demo only: the value the user chose, shown instead of the device value. */
  preview = $state<T | null>(null);
  note = $state<string | null>(null);
  error = $state<string | null>(null);
  busy = $state(false);
  #timer: ReturnType<typeof setTimeout> | undefined;

  async submit(value: T, send: (value: T) => Promise<WriteResult>): Promise<void> {
    this.reset();
    this.busy = true;
    try {
      const result = await send(value);
      if (result.would_send) {
        this.preview = value;
      } else if (!result.sent) {
        this.note = result.message;
      }
      this.#clearLater();
    } catch (err) {
      this.error = err instanceof ApiError ? err.message : 'Die Änderung konnte nicht übertragen werden.';
    } finally {
      this.busy = false;
    }
  }

  reset(): void {
    clearTimeout(this.#timer);
    this.preview = null;
    this.note = null;
    this.error = null;
  }

  #clearLater(): void {
    clearTimeout(this.#timer);
    this.#timer = setTimeout(() => {
      this.preview = null;
      this.note = null;
    }, PREVIEW_MS);
  }
}
