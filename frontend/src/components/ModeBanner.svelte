<script lang="ts">
  import { DEMO_BANNER, LIVE_CONFIRMATION } from '../lib/texts';
  import type { OperatingMode } from '../lib/types';

  let {
    mode,
    confirming,
    busy,
    error,
    onConfirm,
    onCancel,
  }: {
    mode: OperatingMode;
    /** The user asked for live mode; it is switched on only after confirming here. */
    confirming: boolean;
    busy: boolean;
    error: string | null;
    onConfirm: () => void;
    onCancel: () => void;
  } = $props();

  // Focus on the harmless choice, so an accidental Enter does not switch live on.
  let cancelButton = $state<HTMLButtonElement | null>(null);

  $effect(() => {
    if (confirming) {
      cancelButton?.focus();
    }
  });
</script>

{#if confirming}
  <div class="banner live confirm" role="alertdialog" aria-labelledby="live-question">
    <p id="live-question">{LIVE_CONFIRMATION}</p>
    <div class="actions">
      <button type="button" class="button" disabled={busy} bind:this={cancelButton} onclick={onCancel}>Abbrechen</button>
      <button type="button" class="button live-button" disabled={busy} onclick={onConfirm}>
        Live einschalten
      </button>
    </div>
  </div>
{:else if mode === 'demo'}
  <p class="banner info" role="status">{DEMO_BANNER}</p>
{/if}
{#if error}
  <p class="banner error" role="alert">{error}</p>
{/if}

<style>
  .banner.live {
    background: var(--live-bg);
    color: var(--live-text);
  }

  .confirm {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .actions {
    display: flex;
    gap: 8px;
  }

  .live-button {
    border-color: var(--live-button);
    background: var(--live-button);
    color: #ffffff;
  }
</style>
