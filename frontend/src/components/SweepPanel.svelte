<script lang="ts">
  import { ApiError, roomSweep } from '../lib/api';
  import { ask } from '../lib/confirm.svelte';
  import { formatNumber, maybe, moment, signed } from '../lib/format';
  import type { OperatingMode, RoomControlRoom, RoomsPage, SweepPoint } from '../lib/types';
  import StatusPill from './StatusPill.svelte';

  let {
    room,
    appMode,
    onChange,
  }: {
    room: RoomControlRoom;
    appMode: OperatingMode;
    onChange: (page: RoomsPage) => void;
  } = $props();

  let busy = $state(false);
  let error = $state<string | null>(null);

  const sweep = $derived(room.sweep);
  const running = $derived(sweep !== null && sweep.state === 'running');

  // The curve: valve over the offset to the temperature at the thermostat, rising and falling apart.
  const WIDTH = 320;
  const HEIGHT = 150;
  const LEFT = 34;
  const RIGHT = 10;
  const TOP = 10;
  const BOTTOM = 26;
  const points = $derived(sweep?.points ?? []);
  const range = $derived.by(() => {
    const offsets = points.map((point) => point.offset_k);
    const low = Math.floor(Math.min(-1, ...offsets));
    const high = Math.ceil(Math.max(1, ...offsets));
    return { low, high };
  });
  function x(offset: number): number {
    return LEFT + ((offset - range.low) / (range.high - range.low)) * (WIDTH - LEFT - RIGHT);
  }
  function y(valve: number): number {
    return TOP + (1 - valve / 100) * (HEIGHT - TOP - BOTTOM);
  }
  function line(items: SweepPoint[]): string {
    return items.map((point) => `${x(point.offset_k).toFixed(1)},${y(point.valve).toFixed(1)}`).join(' ');
  }
  const rising = $derived(points.filter((point) => point.rising));
  const falling = $derived(points.filter((point) => !point.rising));
  const ticks = $derived(
    Array.from({ length: range.high - range.low + 1 }, (_value, index) => range.low + index),
  );
  const described = $derived(
    points.map((point) => `${signed(point.offset_k)} K ${point.rising ? '↑' : '↓'} ${formatNumber(point.valve, 0)} %`).join(', '),
  );

  async function start(): Promise<void> {
    const confirm = await ask(
      `Schneller Durchlauf ${room.name}: Die App stellt das Thermostat etwa eine halbe Stunde lang alle 2 Minuten ` +
        'um 0,5 bis 1 K höher und danach wieder tiefer (höchstens 20 Befehle, nicht im Tageskontingent) und lernt ' +
        'dabei, wie das Ventil reagiert. Danach geht der Sollwert zurück. Der Raum kann kurz etwas wärmer werden. ' +
        'Starten?',
      'Starten',
    );
    if (confirm) {
      await act(() => roomSweep(room.room_id, 'start', true));
    }
  }

  async function act(call: () => Promise<RoomsPage>): Promise<void> {
    busy = true;
    error = null;
    try {
      onChange(await call());
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Das hat nicht geklappt.';
    } finally {
      busy = false;
    }
  }
</script>

<div class="sweep">
  <span class="title">Schneller Durchlauf</span>
  {#if sweep && running}
    <p>
      <StatusPill label={sweep.state_name} tone="neutral" />
      {sweep.message}
      <span class="hint">
        · {sweep.points.length} Punkte, {sweep.commands} von 20 Befehlen{sweep.setpoint_c !== null
          ? `, Soll ${maybe(sweep.setpoint_c, 1, '°C')}`
          : ''}{sweep.part_of_test ? ' · Teil 1 des Testlaufs' : ''}
      </span>
    </p>
    {#if !sweep.part_of_test}
      <div>
        <button type="button" class="button small" disabled={busy} onclick={() => void act(() => roomSweep(room.room_id, 'cancel', false))}
          >Durchlauf abbrechen</button
        >
      </div>
    {/if}
  {:else}
    {#if sweep}
      <p>
        <StatusPill label={sweep.state_name} tone={sweep.complete ? 'ok' : 'neutral'} />
        {sweep.ended_at ? `${moment(sweep.ended_at)} Uhr` : ''} ({sweep.message})
      </p>
    {/if}
    {#if room.sweep_queued}
      <p class="hint">Wartet: „Alle Räume nacheinander“ kommt gleich zu diesem Raum.</p>
      <div>
        <button type="button" class="button small" disabled={busy} onclick={() => void act(() => roomSweep(room.room_id, 'cancel', false))}
          >Durchläufe abbrechen</button
        >
      </div>
    {:else if appMode !== 'live'}
      <p class="hint">Ein Durchlauf stellt das Thermostat und geht nur im Live-Betrieb.</p>
    {:else}
      <div>
        <button
          type="button"
          class="button small"
          disabled={busy || room.sweep_blocked !== null}
          title={room.sweep_blocked ?? undefined}
          onclick={() => void start()}>Schnellen Durchlauf starten</button
        >
      </div>
      {#if room.sweep_blocked}
        <p class="hint">{room.sweep_blocked}</p>
      {:else}
        <p class="hint">
          Etwa 30 Minuten, ohne Fühlerpaar und ohne warmes Wasser; danach weiß die App, ab wann das Ventil öffnet und
          schließt („Steuern“ wird möglich).
        </p>
      {/if}
    {/if}
  {/if}
  {#if points.length > 1}
    <figure>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`Kurve des Durchlaufs ${room.name}: ${described}`}>
        {#each [0, 50, 100] as level (level)}
          <line class="grid" x1={LEFT} x2={WIDTH - RIGHT} y1={y(level)} y2={y(level)} />
          <text class="axis" x={LEFT - 6} y={y(level) + 4} text-anchor="end">{level} %</text>
        {/each}
        {#each ticks as tick (tick)}
          <text class="axis" x={x(tick)} y={HEIGHT - 8} text-anchor="middle">{signed(tick)}</text>
        {/each}
        {#if rising.length > 1}<polyline class="rising" points={line(rising)} />{/if}
        {#if falling.length > 1}<polyline class="falling" points={line(falling)} />{/if}
        {#each points as point, index (index)}
          <circle class={point.rising ? 'rising' : 'falling'} cx={x(point.offset_k)} cy={y(point.valve)} r="3" />
        {/each}
      </svg>
      <figcaption class="hint">
        Ventil über der Abweichung (Soll minus Temperatur am Thermostat, K):
        <span class="key rising">steigend</span> <span class="key falling">fallend</span>
      </figcaption>
    </figure>
  {/if}
  {#if error}
    <p class="error" role="alert">{error}</p>
  {/if}
</div>

<style>
  .sweep {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .title {
    font-size: 15px;
    font-weight: 600;
  }

  p {
    margin: 0;
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    font-size: 14px;
  }

  p.hint {
    display: block;
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

  polyline {
    fill: none;
    stroke-width: 2;
  }

  polyline.rising,
  circle.rising {
    stroke: var(--heat);
  }

  polyline.falling,
  circle.falling {
    stroke: var(--water);
  }

  circle.rising {
    fill: var(--heat);
  }

  circle.falling {
    fill: var(--water);
  }

  .key::before {
    content: '';
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    margin: 0 4px 0 6px;
    vertical-align: -1px;
  }

  .key.rising::before {
    background: var(--heat);
  }

  .key.falling::before {
    background: var(--water);
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
</style>
