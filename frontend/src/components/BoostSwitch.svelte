<script lang="ts">
  import { WriteFeedback } from '../lib/feedback.svelte';
  import { BOOST_LABELS } from '../lib/texts';
  import type { HotWater, OperatingMode, WriteResult } from '../lib/types';

  let {
    boost,
    writable,
    mode,
    pending,
    send,
  }: {
    boost: NonNullable<HotWater['boost']>;
    writable: boolean;
    mode: OperatingMode;
    /** Switch position sent in live mode that the device has not reported yet. */
    pending: boolean | null;
    send: (active: boolean) => Promise<WriteResult>;
  } = $props();

  const feedback = new WriteFeedback<boolean>();

  const active = $derived(boost === 'active' || boost === 'running');
  const shown = $derived(feedback.preview ?? active);
  const enabled = $derived(writable && !feedback.busy && pending === null);
</script>

<div class="boost">
  <div class="text">
    <span class="title" id="boost-title">Einmalladung</span>
    <span class="hint">Speicher jetzt einmal aufheizen</span>
    {#if feedback.error}
      <span class="error" role="alert">{feedback.error}</span>
    {:else if pending !== null}
      <span class="state" role="status">Wird gesendet …</span>
    {:else if feedback.note}
      <span class="state" role="status">{feedback.note}</span>
    {:else if mode === 'demo' && feedback.preview !== null}
      <span class="state demo">Demo: wird nicht gesendet</span>
    {:else}
      <span class="state">Status: {BOOST_LABELS[boost]}</span>
    {/if}
  </div>
  <button
    type="button"
    class="switch"
    class:on={shown}
    role="switch"
    aria-checked={shown}
    aria-labelledby="boost-title"
    disabled={!enabled}
    onclick={() => void feedback.submit(!shown, send)}
  >
    <span class="knob"></span>
  </button>
</div>

<style>
  .boost {
    align-self: stretch;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding-top: 4px;
  }

  .text {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .title {
    font-size: 15px;
    font-weight: 500;
  }

  .state,
  .error {
    font-size: 13px;
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

  /* 44 px touch target around a 52 × 30 track. */
  .switch {
    flex-shrink: 0;
    position: relative;
    width: 56px;
    height: 44px;
    padding: 0;
    border: 0;
    background: transparent;
    cursor: pointer;
  }

  .switch::before {
    content: '';
    position: absolute;
    left: 2px;
    top: 7px;
    width: 52px;
    height: 30px;
    border-radius: 15px;
    background: var(--control-border);
    transition: background 0.15s;
  }

  .switch.on::before {
    background: var(--water);
  }

  .knob {
    position: absolute;
    left: 5px;
    top: 10px;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    background: #ffffff;
    box-shadow: 0 1px 2px rgb(0 0 0 / 25%);
    transition: transform 0.15s;
  }

  .switch.on .knob {
    transform: translateX(22px);
  }

  .switch:disabled {
    cursor: not-allowed;
    opacity: 0.55;
  }

  .switch:focus-visible {
    outline: 3px solid var(--info-text);
    outline-offset: 2px;
    border-radius: 15px;
  }
</style>
