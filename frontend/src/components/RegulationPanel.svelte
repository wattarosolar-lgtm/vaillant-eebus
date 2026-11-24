<script lang="ts">
  import { ApiError, resetRegulation } from '../lib/api';
  import { formatNumber, maybe, moment, signed } from '../lib/format';
  import type { QualityDay, RoomControlRoom, RoomsPage } from '../lib/types';

  let {
    room,
    onChange,
  }: {
    room: RoomControlRoom;
    onChange: (page: RoomsPage) => void;
  } = $props();

  let busy = $state(false);
  let error = $state<string | null>(null);
  // Open on a wide screen, closed on a phone (only at the start: the user may open and close it).
  const startOpen = window.matchMedia('(min-width: 700px)').matches;

  const regulation = $derived(room.regulation);
  const today = $derived(regulation.quality.find((day) => day.today) ?? null);

  // The demand curve: opening (%) over the outside temperature (-10 ... 15 °C).
  const WIDTH = 320;
  const HEIGHT = 150;
  const LEFT = 34;
  const RIGHT = 22;
  const TOP = 10;
  const BOTTOM = 26;
  const TOP_VALUE = 90;
  function x(outdoor: number): number {
    const bounded = Math.max(-10, Math.min(15, outdoor));
    return LEFT + ((bounded + 10) / 25) * (WIDTH - LEFT - RIGHT);
  }
  function y(value: number): number {
    return TOP + (1 - value / TOP_VALUE) * (HEIGHT - TOP - BOTTOM);
  }
  const curve = $derived(
    regulation.stages.map((stage) => `${x(stage.outdoor_c).toFixed(1)},${y(stage.value).toFixed(1)}`).join(' '),
  );
  const describedCurve = $derived(
    regulation.stages
      .map((stage) => `${signed(stage.outdoor_c, 0)} °C: ${formatNumber(stage.value, 0)} %${stage.learned ? '' : ' (Startwert)'}`)
      .join(', '),
  );

  function deviation(day: QualityDay): string {
    if (day.mean_k === null) {
      return '–';
    }
    if (Math.abs(day.mean_k) < 0.05) {
      return 'am Soll';
    }
    return `${formatNumber(Math.abs(day.mean_k), 1)} K ${day.mean_k > 0 ? 'zu kalt' : 'zu warm'}`;
  }

  function dayName(day: QualityDay): string {
    if (day.today) {
      return 'heute';
    }
    return new Date(`${day.day}T12:00:00`).toLocaleDateString('de-DE', { weekday: 'short', day: '2-digit', month: '2-digit' });
  }

  function heatups(day: QualityDay): string {
    if (day.heatups === 0 && day.heatups_missed === 0) {
      return '–';
    }
    const median = day.heatup_median_min !== null ? ` · ${formatNumber(day.heatup_median_min, 0)} min` : '';
    const missed = day.heatups_missed > 0 ? ` · ${day.heatups_missed}× > 3 h` : '';
    return `${day.heatups}×${median}${missed}`;
  }

  const summary = $derived.by(() => {
    const parts: string[] = [];
    if (regulation.applies && regulation.gain !== null) {
      parts.push(`Verstärkung ${formatNumber(regulation.gain, 0)} %/K`);
    }
    if (regulation.applies && regulation.demand_now !== null) {
      parts.push(`Bedarf jetzt ${formatNumber(regulation.demand_now, 0)} %`);
    }
    if (today && today.mean_abs_k !== null) {
      parts.push(`heute im Mittel ${formatNumber(today.mean_abs_k, 1)} K daneben`);
    }
    if (!regulation.applies) {
      return `Regelgüte (${regulation.quality.length > 0 ? `${regulation.quality.length} Tage` : 'noch keine'})`;
    }
    return parts.length > 0 ? parts.join(' · ') : 'noch nichts gelernt';
  });

  async function reset(): Promise<void> {
    const confirm = window.confirm(
      `Gelerntes der Regelung für ${room.name} zurücksetzen? Der Bedarf je Außentemperatur wird neu aus dem Verlauf ` +
        'berechnet, die Verstärkung geht auf ihren Startwert. Das Kennfeld des Ventils und die Regelgüte bleiben.',
    );
    if (!confirm) {
      return;
    }
    busy = true;
    error = null;
    try {
      onChange(await resetRegulation(room.room_id));
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Das hat nicht geklappt.';
    } finally {
      busy = false;
    }
  }
</script>

<details open={startOpen}>
  <summary>{regulation.applies ? 'Regelung lernt mit: ' : ''}{summary}</summary>
  <div class="body">
    {#if regulation.applies}
      <p class="hint">
        Bedarf = Ventilöffnung, die den Raum am Soll hält, je Außentemperatur (Mittel der letzten 3 h{regulation.outdoor_c !==
        null
          ? `: ${formatNumber(regulation.outdoor_c, 1)} °C`
          : ''}). Gefüllte Punkte sind gelernt, hohle Startwerte ({regulation.basis ?? 'Startwert'}). In „Steuern“ führt
        die App den Bedarf nach, wenn der Raum eine halbe Stunde daneben liegt.
      </p>
      {#if regulation.stages.length > 0}
        <figure>
          <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`Bedarfskurve ${room.name}: ${describedCurve}`}>
            {#each [0, 30, 60, 90] as level (level)}
              <line class="grid" x1={LEFT} x2={WIDTH - RIGHT} y1={y(level)} y2={y(level)} />
              <text class="axis" x={LEFT - 6} y={y(level) + 4} text-anchor="end">{level} %</text>
            {/each}
            {#each regulation.stages as stage (stage.outdoor_c)}
              <text class="axis" x={x(stage.outdoor_c)} y={HEIGHT - 8} text-anchor="middle">{signed(stage.outdoor_c, 0)} °C</text>
            {/each}
            <polyline class="curve" points={curve} />
            {#each regulation.stages as stage (stage.outdoor_c)}
              <circle class:learned={stage.learned} cx={x(stage.outdoor_c)} cy={y(stage.value)} r="4" />
            {/each}
            {#if regulation.outdoor_c !== null && regulation.demand_now !== null}
              <circle class="now" cx={x(regulation.outdoor_c)} cy={y(regulation.demand_now)} r="6" />
            {/if}
          </svg>
          {#if regulation.outdoor_c !== null && regulation.demand_now !== null}
            <figcaption class="hint">
              Jetzt ({formatNumber(regulation.outdoor_c, 1)} °C): {formatNumber(regulation.demand_now, 0)} % Ventil.
            </figcaption>
          {/if}
        </figure>
      {/if}
      <dl class="grid-facts">
        <div>
          <dt>Verstärkung</dt>
          <dd>
            {maybe(regulation.gain, 0, '%/K')}
            <span class="hint">Trend {maybe(regulation.trend, 0, '%')} je K/h</span>
          </dd>
        </div>
        <div>
          <dt>Startwert</dt>
          <dd>
            {maybe(regulation.gain_start, 0, '%/K')}
            {#if regulation.gain_start_reason}<span class="hint">{regulation.gain_start_reason}</span>{/if}
          </dd>
        </div>
        <div class="wide">
          <dt>Letzte Änderung</dt>
          <dd>
            {#if regulation.gain_changed_at}
              {moment(regulation.gain_changed_at)} Uhr: {regulation.gain_reason}
            {:else}
              noch keine (frühestens nach einem Tag mit 12 h „Steuern“)
            {/if}
          </dd>
        </div>
      </dl>
    {:else}
      <p class="hint">
        Am Thermostat gemessen: Hier gilt der feste Ausgleich; die Regelgüte zählt nur Befehle und Ventil.
      </p>
    {/if}

    <span class="title">Regelgüte</span>
    {#if regulation.quality.length === 0}
      <p class="hint">Die App bewertet die Regelung ab „Vorschau“ (alle 5 Minuten, ohne Fenster und Testlauf).</p>
    {:else}
      <div class="table-wrap">
        <table aria-label={`Regelgüte ${room.name}, letzte 7 Tage`}>
          <thead>
            <tr>
              <th scope="col">Tag</th>
              {#if !room.at_thermostat}
                <th scope="col">Abweichung</th>
                <th scope="col">Betrag</th>
                <th scope="col">unter / über Band</th>
                <th scope="col">Überschwinger</th>
                <th scope="col">Hin und her</th>
                <th scope="col">Aufheizen</th>
              {/if}
              <th scope="col">Befehle</th>
              <th scope="col">Bewegungen</th>
              <th scope="col">Ventil Ø / Anschlag</th>
            </tr>
          </thead>
          <tbody>
            {#each regulation.quality as day (day.day)}
              <tr>
                <td>{dayName(day)}{day.in_control ? '' : ' (V)'}</td>
                {#if !room.at_thermostat}
                  <td>{deviation(day)}</td>
                  <td>{maybe(day.mean_abs_k, 1, 'K')}</td>
                  <td>{maybe(day.cold_pct, 0, '%')} / {maybe(day.warm_pct, 0, '%')}</td>
                  <td>{day.overshoots}{day.overshoot_max_k !== null ? ` · ${signed(day.overshoot_max_k)} K` : ''}</td>
                  <td>{day.swings}</td>
                  <td>{heatups(day)}</td>
                {/if}
                <td>{day.commands}</td>
                <td>{day.movements}</td>
                <td>{maybe(day.valve_mean, 0, '%')} / {maybe(day.valve_stop_pct, 0, '%')}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <p class="hint">
        Abweichung gegen den gültigen Soll; Aufheizen nach einer Erhöhung zählt getrennt. (V) = Vorschau: Befehle, die
        die App gesendet hätte. Anschlag = Ventil mindestens 90 % offen (Wasser oder Vorlauf fehlt).
      </p>
    {/if}
    {#if regulation.applies}
      <div>
        <button type="button" class="button small" disabled={busy} onclick={() => void reset()}
          >Gelerntes der Regelung zurücksetzen</button
        >
      </div>
    {/if}
    {#if error}
      <p class="error" role="alert">{error}</p>
    {/if}
  </div>
</details>

<style>
  summary {
    cursor: pointer;
    min-height: 32px;
    font-size: 14px;
    color: var(--text-secondary);
  }

  .body {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .title {
    font-size: 15px;
    font-weight: 600;
  }

  p {
    margin: 0;
    font-size: 14px;
  }

  figure {
    margin: 0;
    max-width: 420px;
  }

  svg {
    width: 100%;
    height: auto;
    display: block;
  }

  .grid {
    stroke: var(--divider);
    stroke-width: 1;
  }

  .axis {
    fill: var(--text-hint);
    font-size: 10px;
  }

  .curve {
    fill: none;
    stroke: var(--heat);
    stroke-width: 2;
  }

  circle {
    fill: var(--surface);
    stroke: var(--heat);
    stroke-width: 2;
  }

  circle.learned {
    fill: var(--heat);
  }

  circle.now {
    fill: none;
    stroke: var(--text);
    stroke-width: 2;
  }

  dl.grid-facts {
    margin: 0;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px 20px;
  }

  dl.grid-facts > div {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  dl.grid-facts > div.wide {
    grid-column: 1 / -1;
  }

  dl.grid-facts dt {
    font-size: 13px;
    color: var(--text-secondary);
  }

  dl.grid-facts dd {
    margin: 0;
    font-size: 15px;
    font-weight: 500;
    overflow-wrap: anywhere;
  }

  dd .hint {
    display: block;
    font-weight: 400;
  }

  .table-wrap {
    overflow-x: auto;
    /* Scrolls inside the card instead of widening it: no width of its own to the layout, the card's width. */
    width: 0;
    min-width: 100%;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }

  th,
  td {
    padding: 4px;
    text-align: left;
    border-bottom: 1px solid var(--divider);
    white-space: nowrap;
  }

  th {
    font-weight: 500;
  }

  .error {
    color: var(--error-text);
    font-size: 14px;
  }

  .button.small {
    min-height: 44px;
    padding: 0 14px;
    font-size: 14px;
  }

  @media (max-width: 420px) {
    dl.grid-facts {
      grid-template-columns: 1fr;
    }
  }
</style>
