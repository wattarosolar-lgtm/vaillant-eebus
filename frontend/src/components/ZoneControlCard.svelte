<script lang="ts">
  import { ApiError, getZoneAutomation, setFunctionMode } from '../lib/api';
  import { maybe, moment, signed } from '../lib/format';
  import { PLANNER_MODE_LABELS } from '../lib/texts';
  import type { FunctionStatus, HemsFunction, HemsStatus, PlannerMode, ZoneControlState } from '../lib/types';
  import StatusPill from './StatusPill.svelte';

  let {
    status,
    changed,
  }: {
    status: HemsStatus;
    /** The energy management's state after a stage of a function was switched. */
    changed: (next: HemsStatus) => void;
  } = $props();

  const MODES: PlannerMode[] = ['off', 'preview', 'control'];
  const HINTS: Record<HemsFunction, string> = {
    zone1:
      'Schaltet die Zone mit Fußbodenheizung aus, solange die Wärmepumpe steht, und sofort wieder auf Auto, wenn sie ' +
      'läuft (ersetzt die eigene Automation in Home Assistant – vor „Steuern“ abschalten). Nicht bei Frostschutz, nicht ' +
      'mit einem zu kalten Raum, Aus frühestens 5 min nach dem letzten Auto.',
    flow:
      'Quick-Veto der Zonen nach Ventilen und Wetterprognose: Programmwert ± Verschiebung bis zur nächsten geplanten ' +
      'Änderung (1–4 h). Vorlauf der Heizkörper-Zone nach dem Engpassraum: höher sofort, wenn ein Raum zu kalt ' +
      'bleibt (erst umverteilen), tiefer höchstens alle 2 h um 0,5 K, solange das Ventil des Engpassraums zwischen ' +
      '10 und 70 % steht.',
    night:
      'Im Nachtfenster (Vorgabe 5:30–10:00): über 3 °C im Mittel der Prognose Zonen aus (bei Vereisungsgefahr erst ' +
      'direkt nach einer Abtauung, sonst bis 7:00 warten), darunter Zone 2 Schritt für Schritt bis zur Minimalleistung ' +
      'der Wärmepumpe, ohne dass sie abschaltet. Aufheizen ab dem Ende des Fensters.',
  };
  const ACTIONS_SHOWN = 10;

  let switching = $state<HemsFunction | null>(null);
  let error = $state<string | null>(null);
  let yaml = $state<string | null>(null);
  let copied = $state(false);

  const control = $derived(status.control);
  const budget = $derived(control?.budget ?? null);

  async function switchFunction(item: FunctionStatus, mode: PlannerMode): Promise<void> {
    if (mode === item.mode || !item.available_modes.includes(mode)) {
      return;
    }
    if (
      mode === 'control' &&
      item.function === 'night' &&
      !window.confirm(
        '„Nachtabsenkung“ auf Steuern: Die App schaltet die Zonen im Nachtfenster aus oder senkt sie per Quick-Veto. ' +
          'Bitte vorher das Zeitprogramm der Zonen in myVAILLANT auf durchgehend Komfort stellen (ohne Absenkfenster), ' +
          'die Zonen auf Auto, und die Automation „Zonen auf Auto, wenn die App schweigt“ anlegen. Fortfahren?',
      )
    ) {
      return;
    }
    if (
      mode === 'control' &&
      item.function === 'zone1' &&
      !window.confirm(
        '„Zone 1 Aus/Auto“ auf Steuern: Die App schaltet Zone 1 dann selbst. Bitte vorher die eigene Automation in Home ' +
          'Assistant abschalten und die Automation „Zonen auf Auto, wenn die App schweigt“ anlegen. Fortfahren?',
      )
    ) {
      return;
    }
    switching = item.function;
    error = null;
    try {
      changed(await setFunctionMode(item.function, mode));
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Die Stufe konnte nicht umgestellt werden.';
    } finally {
      switching = null;
    }
  }

  function zoneText(zone: ZoneControlState): string {
    const parts: string[] = [];
    if (zone.override_c !== null) {
      const until = zone.override_until ? ` bis ${moment(zone.override_until)} Uhr` : '';
      parts.push(`Quick-Veto ${maybe(zone.override_c, 1, '°C')} (${signed(zone.shift_k)} K)${until}`);
    }
    if (zone.off) {
      parts.push(zone.night_off ? 'für die Nacht ausgeschaltet' : 'von der App ausgeschaltet');
    }
    if (zone.preview_shift_k !== null) {
      parts.push(`würde ${signed(zone.preview_shift_k)} K`);
    }
    if (zone.rest_until) {
      parts.push(`in Ruhe bis ${moment(zone.rest_until)} Uhr (${zone.rest_reason ?? 'Änderung von außen'})`);
    } else if (zone.waiting) {
      parts.push(zone.waiting);
    }
    return parts.length > 0 ? parts.join(' · ') : 'folgt ihrem Zeitprogramm';
  }

  async function showAutomation(): Promise<void> {
    try {
      yaml = await getZoneAutomation();
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Die Vorlage konnte nicht geladen werden.';
    }
  }

  async function copy(): Promise<void> {
    if (yaml === null) {
      return;
    }
    try {
      await navigator.clipboard.writeText(yaml);
      copied = true;
      setTimeout(() => (copied = false), 3000);
    } catch {
      error = 'Kopieren geht hier nicht; bitte den Text markieren und kopieren.';
    }
  }
</script>

<section class="card" aria-labelledby="zone-control-title">
  <div class="head">
    <h3 id="zone-control-title">Zonensteuerung</h3>
    <StatusPill label={control?.live ? 'sendet' : 'sendet nicht'} tone={control?.live ? 'warn' : 'neutral'} />
  </div>

  {#each status.functions as item (item.function)}
    <div class="function">
      <div class="function-head">
        <span class="name">{item.name}</span>
        <div class="segments" role="group" aria-label={`Stufe: ${item.name}`}>
          {#each MODES as mode (mode)}
            {@const available = item.available_modes.includes(mode)}
            <button
              type="button"
              class:active={item.mode === mode}
              aria-pressed={item.mode === mode}
              disabled={switching !== null || !available}
              title={available ? undefined : 'Kommt nach Version 1.4.2'}
              onclick={() => void switchFunction(item, mode)}>{PLANNER_MODE_LABELS[mode]}</button
            >
          {/each}
        </div>
      </div>
      <p class="hint">
        {HINTS[item.function]}
        {#if item.effective !== item.mode}
          Wirksam: {PLANNER_MODE_LABELS[item.effective]} (Stufe des Energiemanagements).
        {/if}
      </p>
    </div>
  {/each}

  {#if control?.zone1_halted}
    <p class="warning" role="alert">
      „Zone 1 Aus/Auto“ hält an: Zone 1 wurde heute {control.automation_switches_today}-mal im Takt der Wärmepumpe von
      außen geschaltet. Läuft die eigene Automation noch? Nach dem Abschalten die Stufe hier neu wählen.
    </p>
  {/if}
  {#if error}
    <p class="error" role="alert">{error}</p>
  {/if}
  {#if control}
    {#if control.problems.length > 0}
      <ul class="problems">
        {#each control.problems as problem (problem)}
          <li>{problem}</li>
        {/each}
      </ul>
    {/if}
    {#if budget}
      <p class:warn-text={budget.level !== 'ok'} class="hint">
        Befehle an myVAILLANT heute: {budget.sent} von {budget.limit} (Warnung ab {budget.warn_from}{budget.level ===
        'full'
          ? '; Tagesgrenze erreicht: der Planer gibt die Zonen nur noch zurück'
          : ''}).
        {#if budget.would > 0}
          Vorschau: {budget.would} Befehle wären es heute gewesen.
        {/if}
      </p>
    {/if}
    {#if control.zones.length > 0}
      <dl class="zones">
        {#each control.zones as zone (zone.zone_id)}
          <div><dt>{zone.label}</dt><dd>{zoneText(zone)}</dd></div>
        {/each}
      </dl>
    {/if}
    {#each status.night.filter((item) => item.active || item.off) as night (night.zone_id)}
      <p class="hint">Nacht {night.label}{night.controlled ? '' : ' (würde)'}: {night.text}</p>
    {/each}
    {#if status.minimum.some((band) => band.phases > 0)}
      <p class="hint">
        Kleinste Leistung der Wärmepumpe (Puffer über dem Vorlauf-Soll):
        {status.minimum
          .filter((band) => band.phases > 0)
          .map(
            (band) =>
              `${band.band}: ${band.power_w !== null ? `${Math.round(band.power_w)} W` : 'noch zu wenig'} (${band.phases})`,
          )
          .join(' · ')}
      </p>
    {/if}
    {#each status.flow as flow (flow.zone_id)}
      <p class="hint">
        Vorlauf {status.labels[flow.zone_id] ?? flow.zone_id}{flow.controlled ? '' : ' (würde)'}: Verschiebung
        {signed(flow.offset_k)} K{flow.reason && flow.offset_k !== 0 ? ` (${flow.reason})` : ''}{flow.holding
          ? ` · hält: ${flow.holding}`
          : ''}
      </p>
      {#if flow.curve_advice}
        <p class="hint advice">Empfehlung: {flow.curve_advice}. Die App ändert die Heizkurve nicht.</p>
      {/if}
    {/each}
    {#if control.actions.length > 0}
      <details>
        <summary>Letzte Befehle ({control.actions.length})</summary>
        <ol class="actions">
          {#each control.actions.slice(0, ACTIONS_SHOWN) as action, index (`${action.at}-${index}`)}
            <li>
              <span class="time">{moment(action.at)} Uhr</span>
              <span>{action.text}</span>
              <span class="hint">{action.sent ? 'gesendet' : 'würde'}</span>
            </li>
          {/each}
        </ol>
      </details>
    {/if}
    <details>
      <summary>Automation „Zonen auf Auto, wenn die App schweigt“</summary>
      <div class="details-body">
        <p class="hint">
          Schaltet die App eine Zone aus und fällt dann aus, bliebe die Zone aus. Diese Automation setzt nach 15 Minuten
          ohne Lebenszeichen ({control.lifesign_entity}, {control.lifesign_at
            ? `zuletzt ${moment(control.lifesign_at)} Uhr`
            : 'noch keins'}) die Zonen, die die App ausgeschaltet hat, zurück auf Auto. Anlegen in Home Assistant:
          Einstellungen → Automationen &amp; Szenen → Automation erstellen → ⋮ → „In YAML bearbeiten“, Text einfügen,
          speichern. Ein Quick-Veto der App endet ohnehin von selbst (höchstens 4 h).
        </p>
        {#if yaml === null}
          <div>
            <button type="button" class="button small" onclick={() => void showAutomation()}>Vorlage anzeigen</button>
          </div>
        {:else}
          <pre aria-label="Vorlage der Automation für die Zonen">{yaml}</pre>
          <div class="row">
            <button type="button" class="button small" onclick={() => void copy()}>Kopieren</button>
            <span class="hint" role="status">{copied ? 'Kopiert.' : ''}</span>
          </div>
        {/if}
      </div>
    </details>
  {/if}
</section>

<style>
  .card {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
  }

  h3 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
  }

  .function {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .function-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px 16px;
  }

  .name {
    font-weight: 600;
  }

  .segments {
    min-width: min(100%, 320px);
  }

  .segments > button {
    flex: 1 1 0;
  }

  .segments > button:disabled {
    cursor: not-allowed;
  }

  .hint {
    margin: 0;
  }

  .warning,
  .problems {
    margin: 0;
    padding: 12px 16px;
    border-radius: 12px;
    background: var(--warn-bg);
    color: var(--warn-text);
    font-size: 14px;
  }

  .problems {
    padding-left: 32px;
  }

  .error {
    margin: 0;
    color: var(--error-text);
  }

  .zones {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: 0;
  }

  .zones > div {
    display: grid;
    grid-template-columns: minmax(90px, 160px) 1fr;
    gap: 12px;
  }

  .zones dt {
    font-weight: 600;
  }

  .zones dd {
    margin: 0;
    overflow-wrap: anywhere;
  }

  .actions {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin: 8px 0 0;
    padding-left: 20px;
    font-size: 14px;
  }

  .actions li {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 10px;
  }

  .time {
    font-variant-numeric: tabular-nums;
  }

  .details-body {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 8px;
  }

  pre {
    max-height: 320px;
    margin: 0;
    padding: 12px;
    overflow: auto;
    border-radius: 8px;
    background: var(--info-bg);
    font-size: 12px;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  summary {
    cursor: pointer;
    font-weight: 600;
  }

  @media (max-width: 480px) {
    .zones > div {
      grid-template-columns: 1fr;
      gap: 2px;
    }
  }
</style>
