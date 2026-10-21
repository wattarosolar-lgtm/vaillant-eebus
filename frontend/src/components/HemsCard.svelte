<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiError, getHems } from '../lib/api';
  import { formatNumber } from '../lib/format';
  import { PLANNER_MODE_LABELS } from '../lib/texts';
  import type { HemsStatus } from '../lib/types';
  import HemsTimeline from './HemsTimeline.svelte';
  import StatusPill from './StatusPill.svelte';

  // The planner plans every quarter hour; the card follows a little more often.
  const RELOAD_MS = 60_000;

  let status = $state<HemsStatus | null>(null);
  let error = $state<string | null>(null);

  async function load(): Promise<void> {
    try {
      status = await getHems();
      error = null;
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Der Plan konnte nicht geladen werden.';
    }
  }

  onMount(() => {
    void load();
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') {
        void load();
      }
    }, RELOAD_MS);
    return () => clearInterval(timer);
  });

  const tone = $derived(
    status === null || status.mode === 'off' ? 'neutral' : status.mode === 'control' ? 'ok' : 'warn',
  );
  const clock = new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' });
  const weekday = new Intl.DateTimeFormat('de-DE', { weekday: 'long' });

  function when(iso: string): string {
    const moment = new Date(iso);
    const today = new Date().toDateString() === moment.toDateString();
    return `${today ? '' : `${weekday.format(moment)} `}${clock.format(moment)} Uhr`;
  }

  // Controls without a block, with the planner's reason.
  const notes = $derived(
    status?.plan
      ? Object.entries(status.plan.notes).map(([control, note]) => ({
          control,
          label: status?.labels[control] ?? control,
          note,
        }))
      : [],
  );
</script>

<article class="card hems" aria-labelledby="hems-title">
  <div class="card-head">
    <h2 id="hems-title">Energiemanagement</h2>
    {#if status}
      <StatusPill label={`Planer: ${PLANNER_MODE_LABELS[status.mode]}`} {tone} />
    {/if}
  </div>

  {#if error}
    <p class="hint" role="status">{error}</p>
  {:else if status === null}
    <p class="hint" role="status">Plan wird geladen …</p>
  {:else if status.mode === 'off'}
    <p class="secondary">
      Der Planer kann Warmwasser in PV- und günstige Stunden legen und die Zonen vorheizen. Er ist ausgeschaltet; unter
      <a href="#/einstellungen">Einstellungen → Energiemanagement</a> lässt er sich einrichten und auf „Vorschau“ stellen.
    </p>
  {:else}
    {#if status.mode === 'preview'}
      <p class="banner info" role="status">Vorschau: Der Planer zeigt, was er tun würde, und sendet nichts.</p>
    {:else if status.app_mode === 'demo'}
      <p class="banner info" role="status">Die App ist im Demo-Betrieb: Der Planer sendet nichts.</p>
    {/if}
    {#if status.problems.length > 0}
      <ul class="problems" aria-label="Fehlende Daten">
        {#each status.problems as problem (problem)}
          <li>{problem}</li>
        {/each}
      </ul>
    {/if}

    {#if status.next_action}
      <div class="next">
        <span class="hint">Nächste Aktion</span>
        <strong>{when(status.next_action.at)}: {status.next_action.text}</strong>
        {#if status.next_action.reason}
          <span class="secondary">{status.next_action.reason}</span>
        {/if}
      </div>
    {:else if status.plan}
      <p class="secondary">Bis morgen Abend ist keine Verschiebung geplant.</p>
    {/if}

    {#if status.plan}
      <HemsTimeline plan={status.plan} labels={status.labels} />
    {/if}

    {#if notes.length > 0}
      <dl class="facts">
        {#each notes as item (item.control)}
          <div><dt>{item.label}</dt><dd class="note">{item.note}</dd></div>
        {/each}
      </dl>
    {/if}

    <p class="hint">
      {#if status.demand_tomorrow}
        Strombedarf der Wärmepumpe morgen (geschätzt): {formatNumber(status.demand_tomorrow.kwh, 1)} kWh ± {formatNumber(
          status.demand_tomorrow.spread_kwh,
          1,
        )} kWh bei {formatNumber(status.demand_tomorrow.outdoor_c, 0)} °C außen, gelernt aus {status.demand_tomorrow.days}
        Tagen.
      {:else}
        Der Planer lernt den Strombedarf je Außentemperatur: {status.learned_days} von {status.needed_days} vollständigen
        Heiztagen.
      {/if}
    </p>
  {/if}
</article>

<style>
  .card-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .problems {
    margin: 0;
    padding: 12px 16px 12px 32px;
    border-radius: 12px;
    background: var(--warn-bg);
    color: var(--warn-text);
    font-size: 14px;
  }

  .next {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .next strong {
    font-size: 17px;
    font-weight: 600;
  }

  dd.note {
    color: var(--text-secondary);
    font-weight: 400;
    text-align: right;
  }

  a {
    color: var(--info-text);
  }
</style>
