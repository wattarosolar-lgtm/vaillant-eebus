<script lang="ts">
  import type { Snippet } from 'svelte';

  import { digitsForStep, formatNumber } from '../lib/format';
  import { MODE_LABELS, MODE_ORDER } from '../lib/texts';
  import type { Setpoint } from '../lib/types';

  let {
    title,
    label,
    current,
    subline,
    setpoint,
    mode,
    availableModes,
    color,
    textColor,
    children,
  }: {
    title: string;
    label: string;
    /** Formatted current temperature, or null when the device reports none. */
    current: string | null;
    subline: string;
    setpoint: Setpoint | null;
    mode: string | null;
    availableModes: string[];
    color: string;
    textColor: string;
    children?: Snippet;
  } = $props();

  // Geometry of the design reference: 270° arc starting bottom left.
  const CX = 110;
  const CY = 110;
  const R = 90;
  const START_DEG = 135;
  const SWEEP_DEG = 270;

  function arc(fraction: number): { d: string; x: number; y: number } {
    const f = Math.max(0.002, Math.min(1, fraction));
    const a1 = (START_DEG * Math.PI) / 180;
    const a2 = ((START_DEG + SWEEP_DEG * f) * Math.PI) / 180;
    const x1 = CX + R * Math.cos(a1);
    const y1 = CY + R * Math.sin(a1);
    const x2 = CX + R * Math.cos(a2);
    const y2 = CY + R * Math.sin(a2);
    const large = SWEEP_DEG * f > 180 ? 1 : 0;
    return {
      d: `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${R} ${R} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`,
      x: x2,
      y: y2,
    };
  }

  function degrees(value: number, step: number): string {
    return formatNumber(value, Number.isInteger(value) ? 0 : digitsForStep(step));
  }

  const track = arc(1).d;
  const target = $derived(
    setpoint ? arc((setpoint.value - setpoint.min) / (setpoint.max - setpoint.min || 1)) : null,
  );
  const modes = $derived([
    ...MODE_ORDER.filter((candidate) => availableModes.includes(candidate)),
    ...availableModes.filter((candidate) => !MODE_ORDER.includes(candidate)),
  ]);
  const ariaLabel = $derived(
    `${title}: ${current ? `Ist ${current} °C, ` : ''}${
      setpoint ? `Soll ${formatNumber(setpoint.value, digitsForStep(setpoint.step))} °C` : 'Soll nicht verfügbar'
    }`,
  );
</script>

<article class="card dial-card">
  <div class="head">
    <h2>{title}</h2>
    {#if setpoint}
      <span class="hint-text">Bereich {degrees(setpoint.min, setpoint.step)}–{degrees(setpoint.max, setpoint.step)} °C</span>
    {/if}
  </div>

  <div class="dial">
    <svg width="240" height="220" viewBox="0 0 220 210" role="img" aria-label={ariaLabel}>
      <path d={track} fill="none" stroke="var(--track)" stroke-width="14" stroke-linecap="round" />
      {#if target}
        <path d={target.d} fill="none" stroke={color} stroke-width="14" stroke-linecap="round" />
        <circle cx={target.x} cy={target.y} r="10" fill="#ffffff" stroke={color} stroke-width="3" />
      {/if}
    </svg>
    <div class="center">
      <span class="hint-text">{label}</span>
      {#if current}
        <span class="current">{current}<span class="unit"> °C</span></span>
      {:else}
        <span class="current muted" title="Nicht verfügbar">—</span>
      {/if}
      <span class="hint-text">{subline}</span>
    </div>
  </div>

  <div class="target">
    <span class="hint-text">Solltemperatur</span>
    {#if setpoint}
      <span class="target-value" style:color={textColor}>
        {formatNumber(setpoint.value, digitsForStep(setpoint.step))}<span class="unit"> °C</span>
      </span>
    {:else}
      <span class="target-value muted">nicht verfügbar</span>
    {/if}
  </div>

  <div class="modes">
    <span class="hint-text">Betriebsart</span>
    {#if modes.length > 0}
      <div class="segments" role="group" aria-label={`Betriebsart ${title}: ${MODE_LABELS[mode ?? ''] ?? 'unbekannt'}`}>
        {#each modes as candidate (candidate)}
          <span class="segment" class:active={candidate === mode}>{MODE_LABELS[candidate] ?? candidate}</span>
        {/each}
      </div>
    {:else}
      <span class="muted">nicht verfügbar</span>
    {/if}
  </div>

  {#if children}
    {@render children()}
  {/if}
</article>

<style>
  .dial-card {
    align-items: center;
    gap: 20px;
  }

  .head {
    align-self: stretch;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .hint-text {
    font-size: 13px;
    color: var(--text-secondary);
  }

  .dial {
    position: relative;
    width: 240px;
    height: 220px;
  }

  .center {
    position: absolute;
    left: 0;
    right: 0;
    top: 64px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
  }

  /* The ring narrows towards the bottom; longer notes wrap instead of crossing it. */
  .center .hint-text {
    max-width: 140px;
    text-align: center;
  }

  .current {
    font-size: 44px;
    font-weight: 600;
    line-height: 1.1;
    letter-spacing: -0.02em;
  }

  .unit {
    font-size: 20px;
    font-weight: 500;
  }

  .muted {
    color: var(--text-hint);
  }

  .target {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
  }

  .target-value {
    font-size: 32px;
    font-weight: 600;
  }

  .target-value.muted {
    font-size: 15px;
    font-weight: 500;
  }

  .modes {
    align-self: stretch;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .segments {
    display: flex;
    gap: 4px;
    padding: 4px;
    background: var(--bg);
    border-radius: 12px;
  }

  .segment {
    flex: 1 1 0;
    min-height: 44px;
    display: grid;
    place-items: center;
    border-radius: 9px;
    font-size: 14px;
    font-weight: 500;
    color: var(--text);
  }

  .segment.active {
    background: var(--text);
    color: #ffffff;
  }
</style>
