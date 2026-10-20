<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiError, getPowerHistory } from '../lib/api';
  import { formatNumber } from '../lib/format';
  import { HISTORY_GAPS } from '../lib/texts';
  import type { PowerHistory } from '../lib/types';

  // The backend reads Home Assistant at most once a minute; new buckets come every 5 minutes.
  const RELOAD_MS = 5 * 60_000;
  const HEIGHT = 150;
  const PAD = { top: 10, right: 8, bottom: 24, left: 44 };

  let history = $state<PowerHistory | null>(null);
  let error = $state<string | null>(null);
  let width = $state(0);
  // Bucket under the pointer or chosen with the arrow keys.
  let active = $state<number | null>(null);

  async function load(): Promise<void> {
    try {
      history = await getPowerHistory();
      error = null;
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Der Verlauf konnte nicht geladen werden.';
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

  const watts = $derived(history?.watts ?? []);
  const hasValues = $derived(watts.some((value) => value !== null));
  const start = $derived(history?.start ? new Date(history.start).getTime() : 0);
  const stepMs = $derived((history?.step_seconds ?? 300) * 1000);

  /** Top of the scale in kW: a round value just above the peak. */
  const topKw = $derived.by(() => {
    const peak = (history?.peak_watts ?? 0) / 1000;
    const step = peak <= 3 ? 0.5 : 1;
    return Math.max(step, Math.ceil(peak / step) * step);
  });

  const plotWidth = $derived(Math.max(0, width - PAD.left - PAD.right));
  const plotHeight = HEIGHT - PAD.top - PAD.bottom;

  function x(index: number): number {
    return PAD.left + (watts.length > 1 ? (index / (watts.length - 1)) * plotWidth : 0);
  }

  function y(value: number): number {
    return PAD.top + plotHeight - (value / 1000 / topKw) * plotHeight;
  }

  /** Runs of consecutive values; a gap in the history breaks the line. */
  const runs = $derived.by(() => {
    const result: { index: number; value: number }[][] = [];
    let current: { index: number; value: number }[] = [];
    watts.forEach((value, index) => {
      if (value === null) {
        if (current.length > 0) {
          result.push(current);
        }
        current = [];
      } else {
        current.push({ index, value });
      }
    });
    if (current.length > 0) {
      result.push(current);
    }
    return result;
  });

  function linePath(run: { index: number; value: number }[]): string {
    return run.map((point, i) => `${i === 0 ? 'M' : 'L'}${x(point.index).toFixed(1)},${y(point.value).toFixed(1)}`).join('');
  }

  function areaPath(run: { index: number; value: number }[]): string {
    const first = run.at(0);
    const last = run.at(-1);
    if (!first || !last) {
      return '';
    }
    const base = (PAD.top + plotHeight).toFixed(1);
    return `${linePath(run)}L${x(last.index).toFixed(1)},${base}L${x(first.index).toFixed(1)},${base}Z`;
  }

  const yTicks = $derived([0, topKw / 2, topKw]);

  /** Every six hours on the local clock. */
  const xTicks = $derived.by(() => {
    const ticks: { index: number; label: string }[] = [];
    watts.forEach((_value, index) => {
      const moment = new Date(start + index * stepMs);
      if (moment.getMinutes() === 0 && moment.getHours() % 6 === 0) {
        ticks.push({ index, label: `${String(moment.getHours()).padStart(2, '0')}:00` });
      }
    });
    return ticks;
  });

  const clock = new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' });

  function kw(value: number): string {
    return `${formatNumber(value / 1000, 2)} kW`;
  }

  function describe(index: number): string {
    const from = new Date(start + index * stepMs);
    const to = new Date(start + (index + 1) * stepMs);
    const value = watts[index] ?? null;
    return `${clock.format(from)}–${clock.format(to)} Uhr: ${value === null ? 'keine Daten' : kw(value)}`;
  }

  const summary = $derived(
    history?.mean_watts != null && history.peak_watts != null
      ? `Leistung der letzten 24 Stunden: im Mittel ${kw(history.mean_watts)}, höchstens ${kw(history.peak_watts)}.`
      : 'Leistung der letzten 24 Stunden.',
  );

  function pick(event: PointerEvent): void {
    const box = (event.currentTarget as SVGSVGElement).getBoundingClientRect();
    const ratio = (event.clientX - box.left - PAD.left) / Math.max(1, plotWidth);
    active = Math.min(watts.length - 1, Math.max(0, Math.round(ratio * (watts.length - 1))));
  }

  function lastWithValue(): number {
    for (let index = watts.length - 1; index >= 0; index -= 1) {
      if (watts[index] !== null) {
        return index;
      }
    }
    return watts.length - 1;
  }

  function onKey(event: KeyboardEvent): void {
    // One bucket, or an hour with Page Up/Down.
    const moves: Partial<Record<string, number>> = { ArrowLeft: -1, ArrowRight: 1, PageUp: -12, PageDown: 12 };
    const move = moves[event.key];
    if (move !== undefined) {
      active = Math.min(watts.length - 1, Math.max(0, (active ?? lastWithValue()) + move));
    } else if (event.key === 'Home') {
      active = 0;
    } else if (event.key === 'End') {
      active = watts.length - 1;
    } else if (event.key === 'Escape') {
      active = null;
      return;
    } else {
      return;
    }
    event.preventDefault();
  }

  const tooltipLeft = $derived(active === null ? 0 : Math.min(Math.max(x(active), 70), Math.max(70, width - 70)));
</script>

<figure class="chart">
  <figcaption class="caption">
    <span>Leistung der letzten 24 Stunden in kW</span>
    <span class="hint">aus Home Assistant</span>
  </figcaption>
  {#if error}
    <p class="hint" role="status">{error}</p>
  {:else if history === null}
    <p class="hint" role="status">Verlauf wird geladen …</p>
  {:else if history.gap}
    <p class="hint">{HISTORY_GAPS[history.gap]}</p>
  {:else if !hasValues}
    <p class="hint">Noch kein Verlauf. Home Assistant zeichnet die Leistung ab jetzt auf.</p>
  {:else}
    <p class="sr-only" id="power-chart-summary">{summary}</p>
    <!-- A slider over the time axis: the arrow keys move through the five-minute values. -->
    <div
      class="plot"
      bind:clientWidth={width}
      tabindex="0"
      role="slider"
      aria-label="Zeitpunkt im Verlauf"
      aria-describedby="power-chart-summary"
      aria-valuemin={0}
      aria-valuemax={watts.length - 1}
      aria-valuenow={active ?? lastWithValue()}
      aria-valuetext={describe(active ?? lastWithValue())}
      onkeydown={onKey}
      onblur={() => (active = null)}
    >
      {#if width > 0}
        <svg
          {width}
          height={HEIGHT}
          aria-hidden="true"
          onpointermove={pick}
          onpointerdown={pick}
          onpointerleave={() => (active = null)}
        >
          {#each yTicks as tick (tick)}
            <line class="grid" x1={PAD.left} x2={width - PAD.right} y1={y(tick * 1000)} y2={y(tick * 1000)} />
            <text class="axis" x={PAD.left - 6} y={y(tick * 1000)} text-anchor="end" dominant-baseline="middle"
              >{formatNumber(tick, 1)}</text
            >
          {/each}
          {#each xTicks as tick (tick.index)}
            <text class="axis" x={x(tick.index)} y={HEIGHT - 6} text-anchor="middle">{tick.label}</text>
          {/each}
          {#each runs as run (run.at(0)?.index)}
            <path class="area" d={areaPath(run)} />
            <path class="line" d={linePath(run)} />
          {/each}
          {#if active !== null}
            <line class="cross" x1={x(active)} x2={x(active)} y1={PAD.top} y2={PAD.top + plotHeight} />
            {@const value = watts[active] ?? null}
            {#if value !== null}
              <circle class="dot" cx={x(active)} cy={y(value)} r="4" />
            {/if}
          {/if}
        </svg>
        {#if active !== null}
          <div class="tooltip" style:left={`${tooltipLeft}px`}>{describe(active)}</div>
        {/if}
      {/if}
    </div>
  {/if}
</figure>

<style>
  .chart {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .caption {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    font-size: 13px;
    color: var(--text-secondary);
  }

  .plot {
    position: relative;
    touch-action: pan-y;
    border-radius: 8px;
  }

  .plot:focus-visible {
    outline: 3px solid var(--info-text);
    outline-offset: 2px;
  }

  svg {
    display: block;
    overflow: visible;
  }

  .grid {
    stroke: var(--divider);
    stroke-width: 1;
  }

  .axis {
    fill: var(--text-hint);
    font-size: 11px;
  }

  .line {
    fill: none;
    stroke: var(--heat);
    stroke-width: 2;
    stroke-linejoin: round;
    stroke-linecap: round;
  }

  .area {
    fill: var(--heat);
    fill-opacity: 0.1;
  }

  .cross {
    stroke: var(--text-hint);
    stroke-width: 1;
  }

  .dot {
    fill: var(--heat);
    stroke: var(--surface);
    stroke-width: 2;
  }

  .tooltip {
    position: absolute;
    top: -6px;
    transform: translate(-50%, -100%);
    padding: 4px 10px;
    border-radius: 8px;
    background: var(--text);
    color: #ffffff;
    font-size: 13px;
    white-space: nowrap;
    pointer-events: none;
  }
</style>
