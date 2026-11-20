<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiError, getHems } from '../lib/api';
  import { duration, maybe, moment } from '../lib/format';
  import { PLANNER_MODE_LABELS } from '../lib/texts';
  import type { HemsStatus } from '../lib/types';
  import StatusPill from './StatusPill.svelte';

  // The plan is made every quarter hour; the cards follow a little more often.
  const RELOAD_MS = 60_000;

  let status = $state<HemsStatus | null>(null);
  let error = $state<string | null>(null);

  async function load(): Promise<void> {
    try {
      status = await getHems();
      error = null;
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Das Energiemanagement konnte nicht geladen werden.';
    }
  }

  function loadIfVisible(): void {
    if (document.visibilityState === 'visible') {
      void load();
    }
  }

  onMount(() => {
    void load();
    const timer = setInterval(loadIfVisible, RELOAD_MS);
    document.addEventListener('visibilitychange', loadIfVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', loadIfVisible);
    };
  });

  const cycles = $derived(status?.cycles ?? null);
  // The last days with starts, newest first; today is always shown.
  const recentDays = $derived((cycles?.days ?? []).slice(1, 7).filter((day) => day.starts > 0));
  const bands = $derived((cycles?.bands ?? []).filter((band) => band.starts > 0 || band.defrosts > 0));
  const weekday = new Intl.DateTimeFormat('de-DE', { weekday: 'short', day: '2-digit', month: '2-digit' });

  function runningText(): string {
    if (!cycles || cycles.running === null) {
      return 'Leistung unbekannt';
    }
    const since = cycles.since ? ` seit ${moment(cycles.since)} Uhr` : '';
    return cycles.running ? `läuft${since}` : `steht${since}`;
  }
</script>

<div class="grid">
  <article class="card" aria-labelledby="next-changes-title">
    <div class="card-head">
      <h2 id="next-changes-title">Nächste Änderungen</h2>
      {#if status}
        <StatusPill label={PLANNER_MODE_LABELS[status.mode]} tone={status.mode === 'off' ? 'neutral' : 'warn'} />
      {/if}
    </div>
    {#if error}
      <p class="hint" role="alert">{error}</p>
    {:else if status === null}
      <p class="hint" role="status">Wird geladen …</p>
    {:else if status.mode === 'off'}
      <p class="secondary">
        Das Energiemanagement ist aus. Unter Einstellungen → Energiemanagement auf „Vorschau“ stellen, dann zeigt es hier,
        was es vorhat.
      </p>
    {:else if status.next_changes.length === 0}
      <p class="secondary">In den nächsten 48 Stunden ist keine Änderung geplant.</p>
    {:else}
      <ol class="changes">
        {#each status.next_changes as change (change.at)}
          <li>
            <span class="time">{moment(change.at)} Uhr</span>
            <span class="what">{change.text}</span>
            {#if change.reasons.length > 0}
              <span class="hint">{change.reasons.join(' · ')}</span>
            {/if}
          </li>
        {/each}
      </ol>
    {/if}
    {#if status && status.mode !== 'off'}
      <p class="hint">Nur Vorschau: In dieser Version sendet das Energiemanagement nichts.</p>
    {/if}
    {#if status && status.problems.length > 0 && status.mode !== 'off'}
      <p class="hint warn-text">{status.problems[0]}</p>
    {/if}
    <div><a class="button small link-button" href="#/plan">Ganzer Plan</a></div>
  </article>

  <article class="card" aria-labelledby="cycles-title">
    <div class="card-head">
      <h2 id="cycles-title">Takten und Abtauen</h2>
      {#if status?.frost.active}
        <StatusPill label="Frostschutz aktiv" tone="warn" />
      {/if}
    </div>
    {#if cycles}
      <dl class="pairs">
        <div><dt>Starts heute</dt><dd>{cycles.today.starts}</dd></div>
        <div><dt>Abtauungen heute</dt><dd>{cycles.today.defrosts}</dd></div>
        <div><dt>Lauf (Median)</dt><dd>{duration(cycles.today.run_minutes)}</dd></div>
        <div><dt>Pause (Median)</dt><dd>{duration(cycles.today.pause_minutes)}</dd></div>
      </dl>
      <p class="hint">
        Wärmepumpe {runningText()}. Ziel: höchstens {status?.settings.target_starts ?? '–'} Starts am Tag.
        {#if cycles.last_defrost}
          Letzte Abtauung {moment(cycles.last_defrost.at)} Uhr ({maybe(cycles.last_defrost.minutes, 0, 'min')}, außen
          {maybe(cycles.last_defrost.outdoor_c, 1, '°C')}).
        {/if}
      </p>
      {#if status?.zone1}
        <p class="hint">
          Zone-1-Logik (würde): {status.zone1.would === 'off' ? 'Aus' : status.zone1.would === 'auto' ? 'Auto' : '–'}
          · {status.zone1.reason}
          {#if status.zone1.checked_today > 0}
            Heute {status.zone1.agreed_today} von {status.zone1.checked_today} Wechseln wie die Automation.
          {/if}
        </p>
      {/if}
      {#if recentDays.length > 0}
        <table>
          <caption class="sr-only">Starts und Abtauungen der letzten Tage</caption>
          <thead>
            <tr><th scope="col">Tag</th><th scope="col">Starts</th><th scope="col">Lauf</th><th scope="col">Abtauen</th><th scope="col">außen</th></tr>
          </thead>
          <tbody>
            {#each recentDays as day (day.day)}
              <tr>
                <th scope="row">{weekday.format(new Date(day.day))}</th>
                <td>{day.starts}</td>
                <td>{duration(day.run_minutes)}</td>
                <td>{day.defrosts}</td>
                <td>{maybe(day.outdoor_c, 1, '°C')}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
      {#if bands.length > 0}
        <table>
          <caption class="sr-only">Nach Außentemperatur, letzte 90 Tage</caption>
          <thead>
            <tr><th scope="col">außen</th><th scope="col">Starts</th><th scope="col">Lauf</th><th scope="col">Pause</th><th scope="col">Abtauen</th></tr>
          </thead>
          <tbody>
            {#each bands as band (band.band)}
              <tr>
                <th scope="row">{band.band}</th>
                <td>{band.starts}</td>
                <td>{duration(band.run_minutes)}</td>
                <td>{duration(band.pause_minutes)}</td>
                <td>{band.defrosts}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    {:else if !error}
      <p class="hint" role="status">Wird geladen …</p>
    {/if}
  </article>
</div>

<style>
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    gap: 24px;
    align-items: start;
  }

  .card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
  }

  .changes {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .changes li {
    display: grid;
    grid-template-columns: 8.5em minmax(0, 1fr);
    gap: 2px 12px;
  }

  .changes .time {
    color: var(--text-secondary);
    font-size: 14px;
  }

  .changes .what {
    font-weight: 500;
    overflow-wrap: anywhere;
  }

  .changes .hint {
    grid-column: 2;
  }

  dl.pairs {
    margin: 0;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px 24px;
  }

  dl.pairs > div {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  dl.pairs dt {
    font-size: 13px;
    color: var(--text-secondary);
  }

  dl.pairs dd {
    margin: 0;
    font-size: 18px;
    font-weight: 500;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
  }

  th,
  td {
    padding: 6px 4px;
    text-align: right;
    border-bottom: 1px solid var(--divider);
    white-space: nowrap;
  }

  th:first-child {
    text-align: left;
    font-weight: 500;
  }

  thead th {
    font-size: 12px;
    font-weight: 500;
    color: var(--text-secondary);
  }

  .warn-text {
    color: var(--warn-text);
  }

  .link-button {
    display: inline-flex;
    align-items: center;
    text-decoration: none;
  }

  .button.small {
    min-height: 44px;
    padding: 0 14px;
    font-size: 14px;
  }

  @media (max-width: 420px) {
    .grid {
      grid-template-columns: 1fr;
    }

    .changes li {
      grid-template-columns: 1fr;
    }

    .changes .hint {
      grid-column: 1;
    }
  }
</style>
