<script lang="ts">
  import { STATE_LABELS } from '../lib/texts';
  import type { ConnectionState } from '../lib/types';

  let { connection }: { connection: ConnectionState } = $props();

  const entry = $derived(STATE_LABELS[connection]);
</script>

<span class="pill {entry.tone}">
  {#if entry.tone === 'ok'}
    <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true"><circle cx="4" cy="4" r="4" /></svg>
  {/if}
  {entry.label}
</span>

<style>
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 500;
    white-space: nowrap;
  }

  .ok {
    background: var(--ok-bg);
    color: var(--ok-text);
  }

  .ok circle {
    fill: var(--ok-dot);
  }

  .warn {
    background: var(--warn-bg);
    color: var(--warn-text);
  }

  .error {
    background: var(--error-bg);
    color: var(--error-text);
  }

  .neutral {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text-secondary);
  }
</style>
