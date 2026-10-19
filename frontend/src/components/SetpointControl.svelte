<script lang="ts">
  import { onDestroy } from 'svelte';

  import { WriteFeedback } from '../lib/feedback.svelte';
  import { digitsForStep, formatNumber } from '../lib/format';
  import type { OperatingMode, Setpoint, WriteResult } from '../lib/types';

  let {
    title,
    setpoint,
    textColor,
    mode,
    pending,
    note,
    send,
  }: {
    title: string;
    setpoint: Setpoint | null;
    textColor: string;
    mode: OperatingMode;
    /** Value sent in live mode that the device has not reported yet. */
    pending: number | null;
    /** Explanation shown under the value, e.g. what the setpoint means in auto mode. */
    note?: string | null;
    send: (value: number) => Promise<WriteResult>;
  } = $props();

  /** Clicks are collected and sent once after this pause. */
  const SEND_AFTER_MS = 1200;

  const feedback = new WriteFeedback<number>();
  let draft = $state<number | null>(null);
  let timer: ReturnType<typeof setTimeout> | undefined;

  const shown = $derived(draft ?? feedback.preview ?? setpoint?.value ?? null);
  const enabled = $derived(setpoint !== null && setpoint.writable && !feedback.busy);

  function format(value: number, step: number): string {
    return formatNumber(value, digitsForStep(step));
  }

  function change(direction: 1 | -1): void {
    if (!setpoint || shown === null) {
      return;
    }
    const steps = Math.round((shown - setpoint.min) / setpoint.step) + direction;
    const next = Number((setpoint.min + steps * setpoint.step).toFixed(3));
    if (next < setpoint.min || next > setpoint.max) {
      return;
    }
    feedback.reset();
    draft = next;
    clearTimeout(timer);
    timer = setTimeout(() => void commit(), SEND_AFTER_MS);
  }

  async function commit(): Promise<void> {
    const value = draft;
    if (value === null || !setpoint) {
      return;
    }
    if (value === setpoint.value && pending === null) {
      draft = null;
      return;
    }
    try {
      await feedback.submit(value, send);
    } finally {
      draft = null;
    }
  }

  onDestroy(() => clearTimeout(timer));
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
        onclick={() => change(-1)}>−</button
      >
      <output class="value" style:color={textColor} aria-live="polite" aria-label={`Solltemperatur ${title}`}>
        {format(shown, setpoint.step)}<span class="unit"> °C</span>
      </output>
      <button
        type="button"
        class="round"
        aria-label={`Solltemperatur ${title} erhöhen`}
        disabled={!enabled || shown >= setpoint.max}
        onclick={() => change(1)}>+</button
      >
    </div>
    {#if feedback.error}
      <p class="error" role="alert">{feedback.error}</p>
    {:else if pending !== null}
      <p class="state" role="status">Wird gesendet … ({format(pending, setpoint.step)} °C)</p>
    {:else if feedback.note}
      <p class="state" role="status">{feedback.note}</p>
    {:else if draft !== null}
      <p class="state">Wird gleich übernommen …</p>
    {:else if mode === 'demo'}
      <p class="state demo">Demo: wird nicht gesendet</p>
    {/if}
    {#if !setpoint.writable}
      <p class="state">Am Gerät nicht änderbar</p>
    {/if}
    {#if note}
      <p class="note">{note}</p>
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

  .state.demo {
    color: var(--info-text);
  }

  .note {
    color: var(--text-hint);
  }

  .error {
    color: var(--error-text);
  }
</style>
