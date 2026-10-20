<script lang="ts">
  import { digitsForStep, formatNumber } from '../lib/format';
  import type { Setpoint } from '../lib/types';

  let {
    title,
    setpoint,
    shown,
    textColor,
    enabled,
    unconfirmed,
    preview,
    locked,
    error,
    status,
    onStep,
  }: {
    title: string;
    setpoint: Setpoint | null;
    /** The value to show: the user's draft, the demo preview, a sent value or the device's. */
    shown: number | null;
    textColor: string;
    enabled: boolean;
    /** The value is chosen or sent but not reported by the device yet. */
    unconfirmed: boolean;
    /** Demo preview: chosen, but not sent. */
    preview: boolean;
    /** Why the setpoint cannot be changed right now, e.g. in auto mode; hides the value. */
    locked: string | null;
    error: string | null;
    /** For screen readers only: what happens with the change. */
    status: string | null;
    onStep: (direction: 1 | -1) => void;
  } = $props();

  function format(value: number, step: number): string {
    return formatNumber(value, digitsForStep(step));
  }
</script>

<div class="setpoint">
  <span class="hint-text">Solltemperatur</span>
  {#if setpoint && shown !== null}
    <div class="row">
      <button
        type="button"
        class="round"
        aria-label={`Solltemperatur ${title} senken`}
        disabled={!enabled || shown <= setpoint.min}
        onclick={() => onStep(-1)}>−</button
      >
      {#if locked}
        <output class="value program" aria-label={`Solltemperatur ${title}`}>Zeitprogramm</output>
      {:else}
        <output
          class="value"
          class:unconfirmed
          style:color={preview ? 'var(--info-text)' : textColor}
          aria-live="polite"
          aria-label={`Solltemperatur ${title}`}
        >
          {format(shown, setpoint.step)}<span class="unit"> °C</span>
        </output>
      {/if}
      <button
        type="button"
        class="round"
        aria-label={`Solltemperatur ${title} erhöhen`}
        disabled={!enabled || shown >= setpoint.max}
        onclick={() => onStep(1)}>+</button
      >
    </div>
    <span class="sr-only" role="status">{status ?? ''}</span>
    {#if error}
      <p class="error" role="alert">{error}</p>
    {/if}
    {#if !setpoint.writable}
      <p class="state">Am Gerät nicht änderbar</p>
    {:else if locked}
      <p class="note">{locked}</p>
    {/if}
  {:else}
    <span class="value muted">nicht verfügbar</span>
  {/if}
</div>

<style>
  .setpoint {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
  }

  .hint-text {
    font-size: 13px;
    color: var(--text-secondary);
  }

  .row {
    display: flex;
    align-items: center;
    gap: 20px;
  }

  .round {
    width: 48px;
    height: 48px;
    border-radius: 50%;
    border: 1px solid var(--control-border);
    background: var(--surface);
    color: var(--text);
    font-size: 24px;
    line-height: 1;
    cursor: pointer;
  }

  .round:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }

  .round:focus-visible {
    outline: 3px solid var(--info-text);
    outline-offset: 2px;
  }

  .value {
    min-width: 110px;
    text-align: center;
    font-size: 32px;
    font-weight: 600;
    transition: opacity 0.15s;
  }

  /* Chosen or sent, not yet reported by the device. */
  .value.unconfirmed {
    opacity: 0.55;
  }

  .value.program {
    font-size: 20px;
    font-weight: 500;
    color: var(--text-secondary);
  }

  .value.muted {
    min-width: 0;
    font-size: 15px;
    font-weight: 500;
    color: var(--text-hint);
  }

  .unit {
    font-size: 20px;
    font-weight: 500;
  }

  .state,
  .note,
  .error {
    max-width: 300px;
    text-align: center;
    font-size: 13px;
  }

  .state {
    color: var(--text-secondary);
  }

  .note {
    color: var(--text-hint);
  }

  .error {
    color: var(--error-text);
  }
</style>
