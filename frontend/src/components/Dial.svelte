<script lang="ts">
  import { onDestroy, type Snippet } from 'svelte';

  import { WriteFeedback } from '../lib/feedback.svelte';
  import { digitsForStep, formatNumber } from '../lib/format';
  import type { OperatingMode, Setpoint, WriteResult } from '../lib/types';
  import ModeSegments from './ModeSegments.svelte';
  import SetpointControl from './SetpointControl.svelte';

  let {
    title,
    label,
    current,
    subline,
    setpoint,
    operationMode,
    availableModes,
    modeWritable,
    appMode,
    pendingSetpoint,
    pendingMode,
    setpointLocked = null,
    sendSetpoint,
    sendMode,
    color,
    textColor,
    children,
  }: {
    title: string;
    label: string;
    /** Formatted current temperature, or null when there is none to show. */
    current: string | null;
    subline: string;
    setpoint: Setpoint | null;
    operationMode: string | null;
    availableModes: string[];
    modeWritable: boolean;
    /** Demo or live: decides whether changes are previews or sent. */
    appMode: OperatingMode;
    pendingSetpoint: number | null;
    pendingMode: string | null;
    /** Why the setpoint cannot be changed right now (e.g. auto mode); null if it can. */
    setpointLocked?: string | null;
    sendSetpoint: (value: number, shown: OperatingMode) => Promise<WriteResult>;
    sendMode: (mode: string, shown: OperatingMode) => Promise<WriteResult>;
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
  /** How far from the ring a press still grabs it, in SVG units. */
  const GRAB_DISTANCE = 24;

  /** Changes are collected and sent once after this pause. */
  const SEND_AFTER_MS = 1200;
  /** The gate sends at most one command per control within this time (spec 9). */
  const RATE_LIMIT_MS = 5000;

  const feedback = new WriteFeedback<number>();
  let draft = $state<number | null>(null);
  // The operating mode on screen when the draft was started.
  let draftMode: OperatingMode | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let lastSentAt = 0;
  let dragging = $state(false);
  let svg = $state<SVGSVGElement | null>(null);

  // A change made in one mode is never sent in the other: switching drops it.
  $effect(() => {
    feedback.follow(appMode);
    if (draftMode !== null && appMode !== draftMode) {
      clearTimeout(timer);
      draft = null;
      draftMode = null;
      dragging = false;
    }
  });
  $effect(() => feedback.settle(setpoint?.value ?? null, pendingSetpoint));

  const shown = $derived(draft ?? feedback.preview ?? feedback.sending ?? pendingSetpoint ?? setpoint?.value ?? null);
  // Whether the ring is a slider at all; while a value is being sent it is only paused.
  const adjustable = $derived(setpoint !== null && setpoint.writable && setpointLocked === null && shown !== null);
  const enabled = $derived(adjustable && !feedback.busy);
  const unconfirmed = $derived(
    draft !== null || feedback.busy || feedback.sending !== null || (pendingSetpoint !== null && shown === pendingSetpoint),
  );
  const status = $derived(
    feedback.busy || feedback.sending !== null || pendingSetpoint !== null
      ? 'Wird gesendet'
      : feedback.preview !== null
        ? 'Demo: nicht gesendet'
        : null,
  );

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
    setpoint && shown !== null ? arc((shown - setpoint.min) / (setpoint.max - setpoint.min || 1)) : null,
  );
  const shownText = $derived(
    setpoint && shown !== null ? `${formatNumber(shown, digitsForStep(setpoint.step))} °C` : null,
  );

  /** The nearest value on the device's steps within its range. */
  function snap(value: number): number {
    if (!setpoint) {
      return value;
    }
    const steps = Math.round((value - setpoint.min) / setpoint.step);
    const snapped = Number((setpoint.min + steps * setpoint.step).toFixed(3));
    return Math.min(setpoint.max, Math.max(setpoint.min, snapped));
  }

  function choose(value: number): void {
    if (!setpoint || !enabled) {
      return;
    }
    feedback.reset();
    draft = snap(value);
    draftMode = appMode;
    clearTimeout(timer);
  }

  /** Sends the draft once the user paused, and not before the gate accepts the next command. */
  function sendLater(): void {
    clearTimeout(timer);
    if (draft === null) {
      return;
    }
    const earliest = appMode === 'live' ? lastSentAt + RATE_LIMIT_MS - Date.now() : 0;
    timer = setTimeout(() => void commit(), Math.max(SEND_AFTER_MS, earliest));
  }

  function step(direction: 1 | -1): void {
    if (setpoint && shown !== null) {
      choose(shown + direction * setpoint.step);
      sendLater();
    }
  }

  async function commit(): Promise<void> {
    const value = draft;
    const shownMode = draftMode;
    if (value === null || shownMode === null || !setpoint) {
      return;
    }
    if (value === setpoint.value && pendingSetpoint === null) {
      draft = null;
      draftMode = null;
      return;
    }
    if (shownMode === 'live') {
      lastSentAt = Date.now();
    }
    try {
      await feedback.submit(value, (chosen) => sendSetpoint(chosen, shownMode));
    } finally {
      draft = null;
      draftMode = null;
    }
  }

  /** The value at a pointer position on the ring, or null if the pointer is not on the ring. */
  function valueAt(event: PointerEvent, anywhere: boolean): number | null {
    const matrix = svg?.getScreenCTM();
    if (!setpoint || !matrix) {
      return null;
    }
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    const dx = point.x - CX;
    const dy = point.y - CY;
    if (!anywhere && Math.abs(Math.hypot(dx, dy) - R) > GRAB_DISTANCE) {
      return null;
    }
    let along = ((Math.atan2(dy, dx) * 180) / Math.PI - START_DEG + 720) % 360;
    if (along > SWEEP_DEG) {
      // In the gap at the bottom: the nearer end of the ring.
      along = along - SWEEP_DEG < (360 - SWEEP_DEG) / 2 ? SWEEP_DEG : 0;
    }
    return setpoint.min + (along / SWEEP_DEG) * (setpoint.max - setpoint.min);
  }

  function onPointerDown(event: PointerEvent): void {
    if (!enabled || event.button !== 0) {
      return;
    }
    const value = valueAt(event, false);
    if (value === null) {
      return;
    }
    event.preventDefault();
    svg?.setPointerCapture(event.pointerId);
    dragging = true;
    choose(value);
  }

  function onPointerMove(event: PointerEvent): void {
    if (!dragging || !setpoint || shown === null) {
      return;
    }
    const value = valueAt(event, true);
    // Crossing the gap would jump from one end to the other; stay at the end instead.
    if (value !== null && Math.abs(value - shown) <= (setpoint.max - setpoint.min) / 2) {
      choose(value);
    }
  }

  function onPointerUp(): void {
    if (dragging) {
      dragging = false;
      sendLater();
    }
  }

  function onKeyDown(event: KeyboardEvent): void {
    if (!setpoint || shown === null || !enabled) {
      return;
    }
    const steps: Record<string, number> = {
      ArrowUp: setpoint.step,
      ArrowRight: setpoint.step,
      ArrowDown: -setpoint.step,
      ArrowLeft: -setpoint.step,
      PageUp: 4 * setpoint.step,
      PageDown: -4 * setpoint.step,
    };
    const delta = steps[event.key];
    let next: number;
    if (delta !== undefined) {
      next = shown + delta;
    } else if (event.key === 'Home') {
      next = setpoint.min;
    } else if (event.key === 'End') {
      next = setpoint.max;
    } else {
      return;
    }
    event.preventDefault();
    choose(next);
    sendLater();
  }

  onDestroy(() => clearTimeout(timer));
</script>

<article class="card dial-card">
  <div class="head">
    <h2>{title}</h2>
    {#if setpoint}
      <span class="hint-text">Bereich {degrees(setpoint.min, setpoint.step)}–{degrees(setpoint.max, setpoint.step)} °C</span>
    {/if}
  </div>

  <div class="dial">
    <svg
      bind:this={svg}
      class:slider={adjustable}
      class:busy={adjustable && !enabled}
      class:dragging
      width="240"
      height="220"
      viewBox="0 0 220 210"
      role="slider"
      tabindex={adjustable ? 0 : -1}
      aria-label={`Solltemperatur ${title}`}
      aria-valuemin={setpoint?.min ?? 0}
      aria-valuemax={setpoint?.max ?? 0}
      aria-valuenow={shown ?? 0}
      aria-valuetext={shownText ?? 'nicht verfügbar'}
      aria-disabled={!enabled}
      onpointerdown={onPointerDown}
      onpointermove={onPointerMove}
      onpointerup={onPointerUp}
      onpointercancel={onPointerUp}
      onkeydown={onKeyDown}
    >
      <path d={track} fill="none" stroke="var(--track)" stroke-width="14" stroke-linecap="round" />
      {#if target}
        <path d={target.d} fill="none" stroke={color} stroke-width="14" stroke-linecap="round" class:unconfirmed />
        <circle cx={target.x} cy={target.y} r={adjustable ? 12 : 10} fill="#ffffff" stroke={color} stroke-width="3" />
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

  <SetpointControl
    {title}
    {setpoint}
    {shown}
    {textColor}
    {enabled}
    {unconfirmed}
    preview={feedback.preview !== null && draft === null}
    locked={setpointLocked}
    error={feedback.error}
    {status}
    onStep={step}
  />

  <ModeSegments
    {title}
    current={operationMode}
    {availableModes}
    writable={modeWritable}
    mode={appMode}
    pending={pendingMode}
    send={sendMode}
  />

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

  /* Dragging the ring must not scroll the page on touch screens. */
  .slider {
    touch-action: none;
    cursor: pointer;
  }

  .slider.dragging {
    cursor: grabbing;
  }

  .slider.busy {
    cursor: progress;
  }

  .slider:focus:not(:focus-visible) {
    outline: none;
  }

  .slider:focus-visible {
    outline: 3px solid var(--info-text);
    outline-offset: 2px;
    border-radius: 50%;
  }

  /* Chosen or sent, not yet reported by the device. */
  .unconfirmed {
    opacity: 0.55;
  }

  /* Lets presses on the ring through to the slider below. */
  .center {
    position: absolute;
    left: 0;
    right: 0;
    top: 64px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    pointer-events: none;
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
</style>
