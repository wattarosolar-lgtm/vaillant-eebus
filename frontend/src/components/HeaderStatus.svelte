<script lang="ts">
  import { temperature } from '../lib/format';
  import { operatingStateName } from '../lib/texts';
  import type { ApiState } from '../lib/types';
  import StatusPill from './StatusPill.svelte';

  let { live }: { live: ApiState } = $props();

  const heatPump = $derived(live.connection === 'connected' && live.heat_pump?.available ? live.heat_pump : null);
  const outdoor = $derived(temperature(heatPump?.measurements['outdoor_temperature']));
</script>

<div class="status" aria-label="Status der Wärmepumpe">
  <StatusPill connection={live.connection} />
  {#if heatPump}
    {#if heatPump.heartbeat_ok !== null}
      <span class="pill" class:ok={heatPump.heartbeat_ok} class:warn={!heatPump.heartbeat_ok}>
        {heatPump.heartbeat_ok ? 'Heartbeat OK' : 'Heartbeat fehlt'}
      </span>
    {/if}
    {#if heatPump.operating_state}
      <span class="pill neutral">{operatingStateName(heatPump.operating_state)}</span>
    {/if}
    {#if heatPump.compressor_active !== null}
      <span class="pill neutral" class:secondary-text={!heatPump.compressor_active}>
        {heatPump.compressor_active ? 'Verdichter an' : 'Verdichter aus'}
      </span>
    {/if}
    {#if outdoor}
      <span class="pill dark">Außen <strong>{outdoor} °C</strong></span>
    {/if}
  {/if}
</div>

<style>
  .status {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }

  .pill {
    display: inline-flex;
    align-items: baseline;
    gap: 8px;
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

  .warn {
    background: var(--warn-bg);
    color: var(--warn-text);
  }

  .neutral {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
  }

  .secondary-text {
    color: var(--text-secondary);
  }

  .dark {
    padding: 6px 14px;
    background: var(--text);
    color: #ffffff;
  }

  .dark strong {
    font-size: 16px;
    font-weight: 600;
  }
</style>
