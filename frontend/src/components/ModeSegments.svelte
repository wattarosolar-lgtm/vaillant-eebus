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
    /** Sends the operation mode chosen while shown was the operating mode on screen. */
    send: (mode: string, shown: OperatingMode) => Promise<WriteResult>;
  } = $props();

  const feedback = new WriteFeedback<string>();
  $effect(() => feedback.follow(mode));
  $effect(() => feedback.settle(current, pending));

  // Only the modes the device offers, in the design's order (eco only if offered).
  const modes = $derived([
    ...MODE_ORDER.filter((candidate) => availableModes.includes(candidate)),
    ...availableModes.filter((candidate) => !MODE_ORDER.includes(candidate)),
  ]);
  // Sent or chosen in demo, not reported by the device yet.
  const unconfirmed = $derived(feedback.sending ?? pending);
  const shown = $derived(feedback.preview ?? unconfirmed ?? current);
  const enabled = $derived(writable && !feedback.busy);
  const status = $derived(
    feedback.busy || unconfirmed !== null ? 'Wird gesendet' : feedback.preview !== null ? 'Demo: nicht gesendet' : '',
  );

  function label(candidate: string | null): string {
    return candidate ? (MODE_LABELS[candidate] ?? candidate) : 'unbekannt';
  }

  function choose(candidate: string): void {
    if (candidate !== shown) {
      const onScreen = mode;
      void feedback.submit(candidate, (chosen) => send(chosen, onScreen));
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
          class:pending={candidate === unconfirmed && feedback.preview === null}
          class:preview={candidate === feedback.preview}
          aria-pressed={candidate === shown}
          disabled={!enabled}
          onclick={() => choose(candidate)}>{label(candidate)}</button
        >
      {/each}
    </div>
    <span class="sr-only" role="status">{status}</span>
    {#if feedback.error}
      <p class="error" role="alert">{feedback.error}</p>
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

  /* Sent, not reported by the device yet. */
  .segment.pending {
    opacity: 0.55;
  }

  /* Demo preview: chosen, not sent. */
  .segment.active.preview {
    background: var(--info-text);
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

  .error {
    color: var(--error-text);
  }
</style>
