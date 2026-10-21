<script lang="ts">
  import { formatNumber } from '../lib/format';
  import type { Plan, PlanBlock } from '../lib/types';

  let {
    plan,
    labels,
  }: {
    plan: Plan;
    /** Control ("dhw" or zone ID) -> name. */
    labels: Record<string, string>;
  } = $props();

  const PAD = { left: 80, right: 8 };
  const SURPLUS = { top: 8, height: 80 };
  const PRICE = { top: 104, height: 44 };
  const LANES_TOP = 164;
  const LANE = 26;
  const AXIS = 22;

  let width = $state(0);
  // Slot under the pointer or chosen with the arrow keys.
  let active = $state<number | null>(null);

  const slots = $derived(plan.slots);
  const start = $derived.by(() => {
    const first = slots.at(0);
    return first ? new Date(first.start).getTime() : 0;
  });
  const SLOT_MS = 15 * 60_000;
  const end = $derived(start + slots.length * SLOT_MS);

  // Hot water first, then the zones in the order of the labels.
  const controls = $derived(
    Object.keys(labels).sort((a, b) =>
      a === 'dhw' ? -1 : b === 'dhw' ? 1 : (labels[a] ?? a).localeCompare(labels[b] ?? b),
    ),
  );
  const height = $derived(LANES_TOP + controls.length * LANE + AXIS);
  const plotWidth = $derived(Math.max(0, width - PAD.left - PAD.right));

  function x(time: number): number {
    return PAD.left + ((time - start) / Math.max(1, end - start)) * plotWidth;
  }

  const topKw = $derived.by(() => {
    const peak = Math.max(0, ...slots.map((slot) => slot.surplus_w ?? 0)) / 1000;
    const step = peak <= 3 ? 1 : 2;
    return Math.max(step, Math.ceil(peak / step) * step);
  });

  const prices = $derived(slots.map((slot) => slot.price).filter((price): price is number => price !== null));
  const priceRange = $derived.by(() => {
    if (prices.length === 0) {
      return { low: 0, high: 1 };
    }
    const low = Math.min(...prices);
    const high = Math.max(...prices);
    // A flat price sits in the middle of its strip.
    return high - low < 0.01 ? { low: low - 0.05, high: high + 0.05 } : { low, high };
  });

  function surplusY(watts: number): number {
    return SURPLUS.top + SURPLUS.height - (watts / 1000 / topKw) * SURPLUS.height;
  }

  function priceY(price: number): number {
    const { low, high } = priceRange;
    return PRICE.top + PRICE.height - ((price - low) / (high - low)) * PRICE.height;
  }

  /** Surplus as filled areas; unknown slots break them. */
  const surplusPaths = $derived.by(() => {
    const paths: string[] = [];
    let points: string[] = [];
    let first = 0;
    const base = (SURPLUS.top + SURPLUS.height).toFixed(1);
    const close = (until: number): void => {
      if (points.length > 0) {
        paths.push(`M${x(first).toFixed(1)},${base}${points.join('')}L${x(until).toFixed(1)},${base}Z`);
      }
      points = [];
    };
    slots.forEach((slot, index) => {
      const from = start + index * SLOT_MS;
      if (slot.surplus_w === null) {
        close(from);
        return;
      }
      if (points.length === 0) {
        first = from;
      }
      const y = surplusY(slot.surplus_w).toFixed(1);
      points.push(`L${x(from).toFixed(1)},${y}L${x(from + SLOT_MS).toFixed(1)},${y}`);
    });
    close(end);
    return paths;
  });

  const pricePath = $derived.by(() => {
    let path = '';
    let drawing = false;
    slots.forEach((slot, index) => {
      const from = start + index * SLOT_MS;
      if (slot.price === null) {
        drawing = false;
        return;
      }
      const y = priceY(slot.price).toFixed(1);
      path += `${drawing ? 'L' : 'M'}${x(from).toFixed(1)},${y}L${x(from + SLOT_MS).toFixed(1)},${y}`;
      drawing = true;
    });
    return path;
  });

  const clock = new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' });
  const weekday = new Intl.DateTimeFormat('de-DE', { weekday: 'short' });

  /** Every six hours on the local clock, on a phone every twelve; midnight names the day. */
  const ticks = $derived.by(() => {
    const every = plotWidth < 420 ? 12 : 6;
    const result: { time: number; label: string; midnight: boolean }[] = [];
    for (let time = start; time <= end; time += SLOT_MS) {
      const moment = new Date(time);
      if (moment.getMinutes() === 0 && moment.getHours() % every === 0) {
        const midnight = moment.getHours() === 0;
        result.push({ time, label: midnight ? weekday.format(moment) : clock.format(moment), midnight });
      }
    }
    return result;
  });

  function blockLabel(block: PlanBlock): string {
    if (block.action === 'charge') {
      return `${formatNumber(block.target_c ?? 0, 0)} °C`;
    }
    const shift = block.shift_k ?? 0;
    return `${shift > 0 ? '+' : '−'}${formatNumber(Math.abs(shift), shift % 1 === 0 ? 0 : 1)} K`;
  }

  function ct(price: number | null): string {
    return price === null ? 'kein Preis' : `${formatNumber(price * 100, 1)} ct/kWh`;
  }

  function kw(watts: number | null): string {
    return watts === null ? 'unbekannt' : `${formatNumber(watts / 1000, 1)} kW`;
  }

  function describe(index: number): string {
    const slot = slots[index];
    if (!slot) {
      return '';
    }
    const from = new Date(start + index * SLOT_MS);
    const parts = [`${weekday.format(from)} ${clock.format(from)} Uhr`, `Strom ${ct(slot.price)}`];
    parts.push(`Überschuss ${kw(slot.surplus_w)}`);
    if (slot.outdoor_c !== null) {
      parts.push(`außen ${formatNumber(slot.outdoor_c, 0)} °C`);
    }
    const time = from.getTime();
    for (const block of plan.blocks) {
      if (new Date(block.start).getTime() <= time && time < new Date(block.end).getTime()) {
        parts.push(`${labels[block.control] ?? block.control} ${blockLabel(block)}`);
      }
    }
    return parts.join(' · ');
  }

  const summary = $derived.by(() => {
    if (plan.blocks.length === 0) {
      return 'Plan bis morgen Abend: keine Verschiebung.';
    }
    const items = plan.blocks.map((block) => {
      const from = new Date(block.start);
      return `${labels[block.control] ?? block.control} ${blockLabel(block)} ${weekday.format(from)} ${clock.format(from)} bis ${clock.format(new Date(block.end))} Uhr`;
    });
    return `Plan bis morgen Abend: ${items.join('; ')}.`;
  });

  function pick(event: PointerEvent): void {
    const box = (event.currentTarget as SVGSVGElement).getBoundingClientRect();
    const ratio = (event.clientX - box.left - PAD.left) / Math.max(1, plotWidth);
    active = Math.min(slots.length - 1, Math.max(0, Math.floor(ratio * slots.length)));
  }

  function onKey(event: KeyboardEvent): void {
    // A quarter hour, or two hours with Page Up/Down.
    const moves: Partial<Record<string, number>> = { ArrowLeft: -1, ArrowRight: 1, PageUp: -8, PageDown: 8 };
    const move = moves[event.key];
    if (move !== undefined) {
      active = Math.min(slots.length - 1, Math.max(0, (active ?? 0) + move));
    } else if (event.key === 'Home') {
      active = 0;
    } else if (event.key === 'End') {
      active = slots.length - 1;
    } else if (event.key === 'Escape') {
      active = null;
      return;
    } else {
      return;
    }
    event.preventDefault();
  }

  const activeX = $derived(active === null ? 0 : x(start + (active + 0.5) * SLOT_MS));
  const tooltipLeft = $derived(Math.min(Math.max(activeX, 110), Math.max(110, width - 110)));
</script>

<figure class="timeline">
  <p class="sr-only" id="hems-plan-summary">{summary}</p>
  <div
    class="plot"
    bind:clientWidth={width}
    tabindex="0"
    role="slider"
    aria-label="Zeitpunkt im Plan"
    aria-describedby="hems-plan-summary"
    aria-valuemin={0}
    aria-valuemax={Math.max(0, slots.length - 1)}
    aria-valuenow={active ?? 0}
    aria-valuetext={slots.length > 0 ? describe(active ?? 0) : 'kein Plan'}
    onkeydown={onKey}
    onblur={() => (active = null)}
  >
    {#if width > 0 && slots.length > 0}
      <svg
        {width}
        {height}
        aria-hidden="true"
        onpointermove={pick}
        onpointerdown={pick}
        onpointerleave={() => (active = null)}
      >
        <text class="axis" x={PAD.left - 6} y={SURPLUS.top + 4} text-anchor="end" dominant-baseline="hanging"
          >{formatNumber(topKw, 0)} kW</text
        >
        <text class="axis" x={PAD.left - 6} y={SURPLUS.top + SURPLUS.height} text-anchor="end">0</text>
        <line class="grid" x1={PAD.left} x2={width - PAD.right} y1={SURPLUS.top + SURPLUS.height} y2={SURPLUS.top + SURPLUS.height} />
        {#each surplusPaths as path, index (index)}
          <path class="surplus" d={path} />
        {/each}

        {#if prices.length > 0}
          <text class="axis" x={PAD.left - 6} y={PRICE.top} text-anchor="end" dominant-baseline="hanging"
            >{formatNumber(priceRange.high * 100, 0)}</text
          >
          <text class="axis" x={PAD.left - 6} y={PRICE.top + PRICE.height} text-anchor="end"
            >{formatNumber(priceRange.low * 100, 0)}</text
          >
          <path class="price" d={pricePath} />
        {:else}
          <text class="axis" x={PAD.left} y={PRICE.top + PRICE.height / 2} dominant-baseline="middle">Keine Strompreise</text>
        {/if}

        {#each controls as control, lane (control)}
          {@const top = LANES_TOP + lane * LANE}
          <text class="lane-label" x={PAD.left - 6} y={top + LANE / 2} text-anchor="end" dominant-baseline="middle"
            >{labels[control]}</text
          >
          <rect class="lane" x={PAD.left} y={top + 3} width={plotWidth} height={LANE - 6} rx="5" />
          {#each plan.blocks.filter((block) => block.control === control) as block (block.start)}
            {@const left = x(Math.max(start, new Date(block.start).getTime()))}
            {@const right = x(Math.min(end, new Date(block.end).getTime()))}
            <rect class="block {block.action}" x={left} y={top + 3} width={Math.max(2, right - left)} height={LANE - 6} rx="5" />
            {#if right - left > 34}
              <text class="block-label" x={(left + right) / 2} y={top + LANE / 2} text-anchor="middle" dominant-baseline="middle"
                >{blockLabel(block)}</text
              >
            {/if}
          {/each}
        {/each}

        {#each ticks as tick (tick.time)}
          {#if tick.midnight}
            <line class="midnight" x1={x(tick.time)} x2={x(tick.time)} y1={SURPLUS.top} y2={height - AXIS} />
          {/if}
          <text class="axis" x={x(tick.time)} y={height - 6} text-anchor="middle">{tick.label}</text>
        {/each}
        <line class="now" x1={PAD.left} x2={PAD.left} y1={SURPLUS.top} y2={height - AXIS} />

        {#if active !== null}
          <line class="cross" x1={activeX} x2={activeX} y1={SURPLUS.top} y2={height - AXIS} />
        {/if}
      </svg>
      {#if active !== null}
        <div class="tooltip" style:left={`${tooltipLeft}px`}>{describe(active)}</div>
      {/if}
    {/if}
  </div>
  <figcaption class="legend">
    <span><i class="swatch surplus"></i>PV-Überschuss</span>
    <span><i class="swatch price"></i>Strompreis (ct/kWh)</span>
    <span><i class="swatch charge"></i>Warmwasser laden</span>
    <span><i class="swatch raise"></i>Vorheizen</span>
    <span><i class="swatch lower"></i>Absenken</span>
  </figcaption>
</figure>

<style>
  .timeline {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
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
  }

  .axis,
  .lane-label {
    fill: var(--text-hint);
    font-size: 11px;
  }

  .lane-label {
    fill: var(--text-secondary);
  }

  .surplus {
    fill: var(--sun);
    fill-opacity: 0.45;
  }

  .price {
    fill: none;
    stroke: var(--text-secondary);
    stroke-width: 1.5;
  }

  .lane {
    fill: var(--bg);
  }

  .block.charge {
    fill: var(--water);
  }

  .block.raise {
    fill: var(--heat);
  }

  .block.lower {
    fill: var(--info-text);
  }

  .block-label {
    fill: #ffffff;
    font-size: 11px;
    font-weight: 600;
  }

  .midnight {
    stroke: var(--control-border);
    stroke-dasharray: 3 3;
  }

  .now {
    stroke: var(--text);
    stroke-width: 2;
  }

  .cross {
    stroke: var(--text-hint);
  }

  .tooltip {
    position: absolute;
    top: -6px;
    transform: translate(-50%, -100%);
    max-width: min(320px, 90vw);
    padding: 4px 10px;
    border-radius: 8px;
    background: var(--text);
    color: #ffffff;
    font-size: 13px;
    pointer-events: none;
  }

  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 14px;
    font-size: 12px;
    color: var(--text-secondary);
  }

  .legend span {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }

  .swatch {
    display: inline-block;
    width: 12px;
    height: 12px;
    border-radius: 3px;
  }

  .swatch.surplus {
    background: var(--sun);
    opacity: 0.6;
  }

  .swatch.price {
    height: 2px;
    background: var(--text-secondary);
  }

  .swatch.charge {
    background: var(--water);
  }

  .swatch.raise {
    background: var(--heat);
  }

  .swatch.lower {
    background: var(--info-text);
  }
</style>
