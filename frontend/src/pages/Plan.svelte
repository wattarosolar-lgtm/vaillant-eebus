<script lang="ts">
  import { onMount } from 'svelte';

  import StatusPill from '../components/StatusPill.svelte';
  import { ApiError, downloadPlan, getHems, getPlan } from '../lib/api';
  import { formatNumber, maybe, moment, signed } from '../lib/format';
  import { PLANNER_MODE_LABELS, RISK_LABELS } from '../lib/texts';
  import type { HemsStatus, PlanHour, PlanView } from '../lib/types';

  type View = 'changes' | 'hourly';

  const RELOAD_MS = 60_000;

  let plan = $state<PlanView | null>(null);
  let status = $state<HemsStatus | null>(null);
  let error = $state<string | null>(null);
  let downloadError = $state<string | null>(null);
  let view = $state<View>('changes');
  let showHistory = $state(false);

  async function load(): Promise<void> {
    try {
      [plan, status] = await Promise.all([getPlan(), getHems()]);
      error = null;
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Der Plan konnte nicht geladen werden.';
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

  async function download(): Promise<void> {
    downloadError = null;
    try {
      await downloadPlan();
    } catch (err) {
      downloadError = err instanceof ApiError ? err.message : 'Die CSV-Datei konnte nicht erstellt werden.';
    }
  }

  const zones = $derived(Object.entries(plan?.labels ?? {}));
  const rooms = $derived(Object.entries(plan?.rooms ?? {}));

  /** What tells one hour from the one before: shifts, room setpoints and reasons. */
  function key(hour: PlanHour): string {
    return JSON.stringify([
      hour.zones.map((zone) => [zone.zone_id, zone.shift_k]),
      hour.rooms.map((room) => [room.entity_id, room.setpoint_c]),
      hour.reasons,
    ]);
  }

  const shown = $derived.by(() => {
    const hours = plan?.hours ?? [];
    if (view === 'hourly') {
      return hours;
    }
    return hours.filter((hour, index) => {
      const previous = hours[index - 1];
      return previous === undefined || key(hour) !== key(previous);
    });
  });

  const history = $derived((plan?.history ?? []).filter((record) => record.planned !== null || record.outdoor_c !== null));

  function zoneEntry(hour: PlanHour, zoneId: string) {
    return hour.zones.find((zone) => zone.zone_id === zoneId) ?? null;
  }

  function roomText(hour: PlanHour): string {
    return hour.rooms
      .map((room) => `${plan?.rooms[room.entity_id] ?? room.entity_id} ${formatNumber(room.setpoint_c, 1)}`)
      .join(' · ');
  }

  function forecastText(hour: PlanHour): string {
    const parts = [maybe(hour.outdoor_c, 1, '°C')];
    if (hour.defrost && hour.defrost !== 'none') {
      parts.push(`Abtauen ${RISK_LABELS[hour.defrost]}`);
    }
    if (hour.heat_w !== null) {
      parts.push(`Wärme ${formatNumber(hour.heat_w / 1000, 1)} kW`);
    }
    if (hour.electricity_w !== null) {
      parts.push(`Strom ${formatNumber(hour.electricity_w / 1000, 2)} kW`);
    }
    return parts.join(' · ');
  }
</script>

<div class="page">
  <div class="section-head">
    <h2>Plan</h2>
    {#if status}
      <StatusPill label={PLANNER_MODE_LABELS[status.mode]} tone={status.mode === 'off' ? 'neutral' : 'warn'} />
    {/if}
  </div>

  {#if error}
    <p class="banner error" role="alert">{error}</p>
  {:else if plan === null || status === null}
    <p class="secondary" role="status">Plan wird geladen …</p>
  {:else if status.mode === 'off'}
    <section class="card">
      <p class="secondary">
        Das Energiemanagement ist aus und plant nicht. Unter Einstellungen → Energiemanagement auf „Vorschau“ stellen.
      </p>
      <div><a class="button small link-button" href="#/einstellungen">Zu den Einstellungen</a></div>
    </section>
  {:else}
    <section class="card">
      <p class="secondary">
        Was das Energiemanagement in den nächsten 48 Stunden je Stunde vorhat, mit Grund. Gesendet wird nur, was eine
        Funktion auf „Steuern“ plant (Einstellungen → Energiemanagement → Zonensteuerung); alles andere ist Vorschau.
        Abtaurisiko und Blockbetrieb zeigt der Plan nur (kommen mit 1.6.0).
        {#if plan.created_at}Geplant {moment(plan.created_at)} Uhr.{/if}
      </p>
      {#if status.problems.length > 0}
        <ul class="problems" aria-label="Fehlende Daten">
          {#each status.problems as problem (problem)}
            <li>{problem}</li>
          {/each}
        </ul>
      {/if}
      <div class="controls">
        <div class="segments" role="group" aria-label="Ansicht">
          <button type="button" class:active={view === 'changes'} aria-pressed={view === 'changes'} onclick={() => (view = 'changes')}
            >Nur Änderungen</button
          >
          <button type="button" class:active={view === 'hourly'} aria-pressed={view === 'hourly'} onclick={() => (view = 'hourly')}
            >Stündlich</button
          >
        </div>
        <label class="check">
          <input type="checkbox" bind:checked={showHistory} />
          <span>Rückblick 24 h</span>
        </label>
        <button type="button" class="button small" onclick={() => void download()}>CSV herunterladen</button>
      </div>
      {#if downloadError}
        <p class="error" role="alert">{downloadError}</p>
      {/if}
    </section>

    {#if showHistory}
      <section class="card" aria-labelledby="history-title">
        <h3 id="history-title">Rückblick: geplant und gemessen</h3>
        {#if history.length === 0}
          <p class="hint">Noch kein Rückblick: Er füllt sich Stunde für Stunde.</p>
        {:else}
          <div class="scroll" role="region" aria-label="Rückblick" tabindex="-1">
            <table>
              <thead>
                <tr>
                  <th scope="col">Zeit</th>
                  {#each zones as [zoneId, label] (zoneId)}
                    <th scope="col">{label}: geplant</th>
                    <th scope="col">{label}: Soll</th>
                  {/each}
                  <th scope="col">Außen Prognose</th>
                  <th scope="col">Außen gemessen</th>
                  <th scope="col">Starts</th>
                  <th scope="col">Abtauen</th>
                  <th scope="col">Strom</th>
                </tr>
              </thead>
              <tbody>
                {#each history as record (record.start)}
                  <tr>
                    <th scope="row">{moment(record.start)}</th>
                    {#each zones as [zoneId] (zoneId)}
                      {@const planned = record.planned ? zoneEntry(record.planned, zoneId) : null}
                      <td>{planned ? `${signed(planned.shift_k)} K` : '–'}</td>
                      <td>{maybe(record.setpoints[zoneId], 1, '°C')}</td>
                    {/each}
                    <td>{maybe(record.planned?.outdoor_c, 1, '°C')}</td>
                    <td>{maybe(record.outdoor_c, 1, '°C')}</td>
                    <td>{record.starts}</td>
                    <td>{record.defrosts}</td>
                    <td>{maybe(record.electricity_kwh, 2, 'kWh')}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </section>
    {/if}

    <section class="card" aria-labelledby="plan-title">
      <h3 id="plan-title">Nächste 48 Stunden</h3>
      {#if shown.length === 0}
        <p class="hint">Noch kein Plan: Es fehlt die Wetterprognose oder die Wärmepumpe ist nicht verbunden.</p>
      {:else}
        <div class="scroll" role="region" aria-label="Plan" tabindex="-1">
          <table>
            <thead>
              <tr>
                <th scope="col">Zeit</th>
                {#each zones as [zoneId, label] (zoneId)}
                  <th scope="col">{label}</th>
                {/each}
                {#if rooms.length > 0}
                  <th scope="col">Räume (Soll °C)</th>
                {/if}
                <th scope="col" class="reason-col">Grund</th>
                <th scope="col">Prognose</th>
                <th scope="col">Stand</th>
              </tr>
            </thead>
            <tbody>
              {#each shown as hour (hour.start)}
                <tr class:frost={hour.frost}>
                  <th scope="row">{moment(hour.start)}</th>
                  {#each zones as [zoneId] (zoneId)}
                    {@const entry = zoneEntry(hour, zoneId)}
                    <td>
                      {#if entry?.off}
                        <span class="shift">Aus</span>
                        <span class="sub">Nachtabsenkung</span>
                      {:else if entry}
                        <span class="shift">{signed(entry.shift_k)} K</span>
                        {#if entry.target_c !== null}<span class="sub">Soll {formatNumber(entry.target_c, 1)} °C</span>{/if}
                        {#if entry.flow_c !== null}<span class="sub">Vorlauf ~{formatNumber(entry.flow_c, 0)} °C</span>{/if}
                      {:else}
                        –
                      {/if}
                    </td>
                  {/each}
                  {#if rooms.length > 0}
                    <td class="rooms">{roomText(hour)}</td>
                  {/if}
                  <td class="reason">{hour.reasons.join(' · ') || '–'}</td>
                  <td class="forecast">{forecastText(hour)}</td>
                  <td>Vorschau</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </section>
  {/if}
</div>

<style>
  .page {
    display: flex;
    flex-direction: column;
    gap: 24px;
    min-width: 0;
  }

  .section-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
  }

  .section-head h2 {
    font-size: 20px;
    font-weight: 600;
  }

  .card h3 {
    font-size: 18px;
    font-weight: 600;
  }

  .controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px 20px;
  }

  .check {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    min-height: 44px;
    cursor: pointer;
  }

  .check input {
    width: 20px;
    height: 20px;
  }

  .problems {
    margin: 0;
    padding: 12px 16px 12px 32px;
    border-radius: 12px;
    background: var(--warn-bg);
    color: var(--warn-text);
    font-size: 14px;
  }

  /* The table scrolls inside its card; the page itself never scrolls sideways. */
  .scroll {
    overflow-x: auto;
    max-width: 100%;
  }

  table {
    border-collapse: collapse;
    font-size: 14px;
    min-width: 100%;
  }

  th,
  td {
    padding: 8px 10px;
    text-align: left;
    vertical-align: top;
    border-bottom: 1px solid var(--divider);
  }

  thead th {
    font-size: 12px;
    font-weight: 500;
    color: var(--text-secondary);
    white-space: nowrap;
  }

  tbody th {
    font-weight: 500;
    white-space: nowrap;
    position: sticky;
    left: 0;
    background: var(--surface);
  }

  .shift {
    font-weight: 600;
    white-space: nowrap;
  }

  .sub {
    display: block;
    color: var(--text-secondary);
    font-size: 12px;
    white-space: nowrap;
  }

  .reason {
    min-width: 220px;
    max-width: 360px;
  }

  .rooms,
  .forecast {
    min-width: 160px;
    color: var(--text-secondary);
  }

  tr.frost th {
    box-shadow: inset 3px 0 0 var(--water);
  }

  .error {
    color: var(--error-text);
    font-size: 14px;
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
</style>
