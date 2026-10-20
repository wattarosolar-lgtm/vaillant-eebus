// What a control shows after the user changed it: the demo preview, the value
// on its way to the device, or an error. The values themselves always come
// from the device; a preview only stands for a few seconds (spec 9).

import { ApiError } from './api';
import type { OperatingMode, WriteResult } from './types';

/** How long a demo preview stays visible. */
export const PREVIEW_MS = 5000;
/** Longest a sent value is shown before the gate's pending entry or the device's report takes over. */
const SENDING_MS = 5000;

export class WriteFeedback<T> {
  /** Demo only: the value the user chose, shown instead of the device value. */
  preview = $state<T | null>(null);
  /** Live only: the value being sent, until the gate or the device reports it. */
  sending = $state<T | null>(null);
  error = $state<string | null>(null);
  busy = $state(false);
  #previewTimer: ReturnType<typeof setTimeout> | undefined;
  #sendingTimer: ReturnType<typeof setTimeout> | undefined;
  #mode: OperatingMode | null = null;

  /** Call with the operating mode on screen: what was shown in one mode is cleared when it changes. */
  follow(mode: OperatingMode): void {
    if (this.#mode !== null && this.#mode !== mode) {
      this.reset();
    }
    this.#mode = mode;
  }

  /**
   * Call with the value the device reports and the gate's pending value: once
   * either shows the sent value, the control no longer needs to hold it.
   */
  settle(reported: T | null, pending: T | null): void {
    if (this.sending !== null && !this.busy && (reported === this.sending || pending === this.sending)) {
      this.#clearSending();
    }
  }

  async submit(value: T, send: (value: T) => Promise<WriteResult>): Promise<void> {
    this.reset();
    this.busy = true;
    this.sending = value;
    try {
      const result = await send(value);
      if (result.would_send) {
        this.#clearSending();
        this.preview = value;
        clearTimeout(this.#previewTimer);
        this.#previewTimer = setTimeout(() => (this.preview = null), PREVIEW_MS);
      } else if (result.sent) {
        clearTimeout(this.#sendingTimer);
        this.#sendingTimer = setTimeout(() => (this.sending = null), SENDING_MS);
      } else {
        // Not sent because the device already has the value.
        this.#clearSending();
      }
    } catch (err) {
      this.#clearSending();
      this.error = err instanceof ApiError ? err.message : 'Die Änderung konnte nicht übertragen werden.';
    } finally {
      this.busy = false;
    }
  }

  reset(): void {
    clearTimeout(this.#previewTimer);
    this.preview = null;
    this.error = null;
    this.#clearSending();
  }

  #clearSending(): void {
    clearTimeout(this.#sendingTimer);
    this.sending = null;
  }
}
