<script lang="ts">
  import { WriteFeedback } from '../lib/feedback.svelte';
  import { MODE_LABELS, MODE_ORDER } from '../lib/texts';
  import type { OperatingMode, WriteResult } from '../lib/types';

  let {
    title,
    current,
    availableModes,
    writable,
    mode,
    pending,
    send,
  }: {
    title: string;
    current: string | null;
    availableModes: string[];
    writable: boolean;
    mode: OperatingMode;
    /** Mode sent in live mode that the device has not reported yet. */
    pending: string | null;
    send: (mode: string) => Promise<WriteResult>;
  } = $props();

  const feedback = new WriteFeedback<string>();

  // Only the modes the device offers, in the design's order (eco only if offered).
  const modes = $derived([
    ...MODE_ORDER.filter((candidate) => availableModes.includes(candidate)),
    ...availableModes.filter((candidate) => !MODE_ORDER.includes(candidate)),
  ]);
  const shown = $derived(feedback.preview ?? current);
  const enabled = $derived(writable && !feedback.busy && pending === null);

  function label(candidate: string | null): string {
    return candidate ? (MODE_LABELS[candidate] ?? candidate) : 'unbekannt';
  }

  function choose(candidate: string): void {
    if (candidate !== shown) {
      void feedback.submit(candidate, send);
    }
  }
</script>

<div class="modes">
  <span class="hint-text">Betriebsart</span>
  {#if modes.length > 0}
    <div class="segments" role="group" aria-label={`Betriebsart ${title}: ${label(current)}`}>
      {#each modes as candidate (candidate)}
        <button
          type="button"
          class="segment"
          class:active={candidate === shown}
          class:pending={candidate === pending}
          aria-pressed={candidate === shown}
          disabled={!enabled}
          onclick={() => choose(candidate)}>{label(candidate)}</button
        >
      {/each}
    </div>
    {#if feedback.error}
      <p class="error" role="alert">{feedback.error}</p>
    {:else if pending !== null}
      <p class="state" role="status">Wird gesendet … ({label(pending)})</p>
    {:else if feedback.note}
      <p class="state" role="status">{feedback.note}</p>
    {:else if mode === 'demo' && feedback.preview !== null}
      <p class="state demo">Demo: wird nicht gesendet</p>
    {:else if !writable}
      <p class="state">Am Gerät nicht änderbar</p>
    {/if}
  {:else}
    <span class="muted">nicht verfügbar</span>
  {/if}
</div>

<style>
  .modes {
    align-self: stretch;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .hint-text {
    font-size: 13px;
    color: var(--text-secondary);
  }

  .segments {
    display: flex;
    gap: 4px;
    padding: 4px;
    background: var(--bg);
    border-radius: 12px;
  }

  .segment {
    flex: 1 1 0;
    min-height: 44px;
    border: 0;
    border-radius: 9px;
    background: transparent;
    font-size: 14px;
    font-weight: 500;
    color: var(--text);
    cursor: pointer;
  }

  .segment.active {
    background: var(--text);
    color: #ffffff;
  }

  .segment.pending {
    outline: 2px dashed var(--text-secondary);
    outline-offset: -4px;
  }

  .segment:disabled {
    cursor: not-allowed;
  }

  .segment:disabled:not(.active) {
    color: var(--text-hint);
  }

  .segment:focus-visible {
    outline: 3px solid var(--info-text);
    outline-offset: 2px;
  }

  .muted {
    color: var(--text-hint);
  }

  .state,
  .error {
    font-size: 13px;
    text-align: center;
  }

  .state {
    color: var(--text-secondary);
  }

  .state.demo {
    color: var(--info-text);
  }

  .error {
    color: var(--error-text);
  }
</style>
