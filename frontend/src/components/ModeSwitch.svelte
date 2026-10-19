<script lang="ts">
  import type { OperatingMode } from '../lib/types';

  let {
    mode,
    disabled,
    onRequest,
  }: {
    mode: OperatingMode;
    disabled: boolean;
    /** Asks to switch; live is only switched on after the user confirmed it. */
    onRequest: (mode: OperatingMode) => void;
  } = $props();
</script>

<div class="switch" role="group" aria-label="Betriebsmodus der App">
  <button
    type="button"
    class:active={mode === 'demo'}
    aria-pressed={mode === 'demo'}
    {disabled}
    onclick={() => mode !== 'demo' && onRequest('demo')}>Demo</button
  >
  <button
    type="button"
    class="live"
    class:active={mode === 'live'}
    aria-pressed={mode === 'live'}
    {disabled}
    onclick={() => mode !== 'live' && onRequest('live')}>Live</button
  >
</div>

<style>
  .switch {
    display: inline-flex;
    gap: 4px;
    padding: 4px;
    background: var(--segment-bg);
    border-radius: 12px;
  }

  button {
    min-width: 64px;
    min-height: 40px;
    padding: 0 14px;
    border: 0;
    border-radius: 9px;
    background: transparent;
    color: var(--text-secondary);
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
  }

  button.active {
    background: var(--text);
    color: #ffffff;
  }

  button.live.active {
    background: var(--live-button);
  }

  button:disabled {
    cursor: wait;
  }

  button:focus-visible {
    outline: 3px solid var(--info-text);
    outline-offset: 2px;
  }
</style>
