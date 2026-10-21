<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiError, getHems, getLocation, saveHemsSettings, setHemsMode } from '../lib/api';
  import { CONTROL_CONFIRMATION, PLANNER_MODE_HINTS, PLANNER_MODE_LABELS, TARIFF_LABELS } from '../lib/texts';
  import type {
    EntityKind,
    HemsSettings,
    HemsStatus,
    PlannerMode,
    TariffKind,
    TimeWindow,
    ZoneLimits,
  } from '../lib/types';
  import EntityPicker from './EntityPicker.svelte';
  import StatusPill from './StatusPill.svelte';

  let {
    zones,
    homeAssistantAvailable,
  }: {
    /** Zones the heat pump reports. */
    zones: { id: string; label: string }[];
    homeAssistantAvailable: boolean;
  } = $props();

  const MODES: PlannerMode[] = ['off', 'preview', 'control'];
  const TARIFFS: TariffKind[] = ['fixed', 'dynamic', 'fixed_14a'];
  const SHIFTS = [0, 0.5, 1, 1.5, 2, 2.5, 3];
  const WINDOWS_MAX = 4;
  const DEFAULT_LIMITS = { up_k: 1, down_k: 1 };

  let status = $state<HemsStatus | null>(null);
  let draft = $state<HemsSettings | null>(null);
  // Friendly names of entities picked in this visit; the settings only know the IDs.
  let names = $state<Record<string, string>>({});
  let loadError = $state<string | null>(null);
  let saveError = $state<string | null>(null);
  let modeError = $state<string | null>(null);
  let locationError = $state<string | null>(null);
  let saving = $state(false);
  let switching = $state(false);
  let justSaved = $state(false);
  let confirmingControl = $state(false);

  function clone(settings: HemsSettings): HemsSettings {
    return JSON.parse(JSON.stringify(settings)) as HemsSettings;
  }

  function withoutMode(settings: HemsSettings): string {
    return JSON.stringify({ ...settings, mode: null });
  }

  const changed = $derived(status !== null && draft !== null && withoutMode(draft) !== withoutMode(status.settings));
  // Numbers the backend needs; an emptied field must be filled again.
  const complete = $derived(
    draft !== null &&
      [draft.tariff.surcharge_ct, draft.tariff.vat_percent, draft.battery_min_soc, draft.dhw_max_c, draft.heating_limit_c].every(
        (value) => typeof value === 'number' && Number.isFinite(value),
      ),
  );

  onMount(async () => {
    try {
      const loaded = await getHems();
      status = loaded;
      draft = clone(loaded.settings);
    } catch (err) {
      loadError = err instanceof ApiError ? err.message : 'Das Energiemanagement konnte nicht geladen werden.';
    }
  });

  function edited(): void {
    justSaved = false;
  }

  function requestMode(mode: PlannerMode): void {
    if (status === null || mode === status.mode) {
      return;
    }
    if (mode === 'control') {
      confirmingControl = true;
      return;
    }
    void switchMode(mode);
  }

  async function switchMode(mode: PlannerMode): Promise<void> {
    switching = true;
    modeError = null;
    try {
      // Only the stage changes; unsaved settings stay in the form.
      status = await setHemsMode(mode);
      confirmingControl = false;
    } catch (err) {
      modeError = err instanceof ApiError ? err.message : 'Die Stufe konnte nicht umgestellt werden.';
    } finally {
      switching = false;
    }
  }

  async function save(): Promise<void> {
    if (draft === null) {
      return;
    }
    saving = true;
    saveError = null;
    try {
      const saved = await saveHemsSettings(draft);
      status = saved;
      draft = clone(saved.settings);
      justSaved = true;
    } catch (err) {
      saveError = err instanceof ApiError ? err.message : 'Die Einstellungen konnten nicht gespeichert werden.';
    } finally {
      saving = false;
    }
  }

  async function takeLocation(): Promise<void> {
    locationError = null;
    try {
      const location = await getLocation();
      if (draft) {
        draft.latitude = location.latitude;
        draft.longitude = location.longitude;
        edited();
      }
    } catch (err) {
      locationError = err instanceof ApiError ? err.message : 'Der Standort konnte nicht gelesen werden.';
    }
  }

  function limits(zoneId: string): ZoneLimits {
    return draft?.zones.find((zone) => zone.zone_id === zoneId) ?? { zone_id: zoneId, ...DEFAULT_LIMITS };
  }

  function setLimit(zoneId: string, key: 'up_k' | 'down_k', value: number): void {
    if (draft === null) {
      return;
    }
    const next = { ...limits(zoneId), [key]: value };
    draft.zones = [...draft.zones.filter((zone) => zone.zone_id !== zoneId), next];
    edited();
  }

  function addWindow(list: TimeWindow[]): void {
    list.push({ start: '17:00', end: '20:00' });
    edited();
  }

  function removeWindow(list: TimeWindow[], index: number): void {
    list.splice(index, 1);
    edited();
  }

  /** "+1,5 K"; no sign for 0. */
  function shiftLabel(sign: string, value: number): string {
    return `${value === 0 ? '' : sign}${String(value).replace('.', ',')} K`;
  }
</script>

{#snippet entity(label: string, kind: EntityKind, value: string | null, set: (value: string | null) => void)}
  <div class="entity-field">
    <span class="field-label">{label}</span>
    {#if value}
      <div class="row-end">
        <span>
          {#if names[value]}{names[value]}{/if}
          <code>{value}</code>
        </span>
        <button
          type="button"
          class="button small"
          aria-label={`${label} entfernen`}
          onclick={() => {
            set(null);
            edited();
          }}>Entfernen</button
        >
      </div>
    {:else if homeAssistantAvailable}
      <EntityPicker
        {kind}
        label={`${label} wählen`}
        onPick={(choice) => {
          names[choice.entity_id] = choice.name;
          set(choice.entity_id);
          edited();
        }}
      />
    {:else}
      <p class="hint">Nicht gewählt; die Auswahl gibt es nur, wenn die App in Home Assistant läuft.</p>
    {/if}
  </div>
{/snippet}

{#snippet windows(title: string, list: TimeWindow[])}
  <fieldset class="windows">
    <legend>{title}</legend>
    {#each list as window, index (index)}
      <div class="window">
        <label>von <input type="time" bind:value={window.start} oninput={edited} /></label>
        <label>bis <input type="time" bind:value={window.end} oninput={edited} /></label>
        <button
          type="button"
          class="button small"
          aria-label={`${title}: Zeitfenster ${index + 1} entfernen`}
          onclick={() => removeWindow(list, index)}>Entfernen</button
        >
      </div>
    {/each}
    {#if list.length < WINDOWS_MAX}
      <div>
        <button
          type="button"
          class="button small"
          aria-label={`${title}: Zeitfenster hinzufügen`}
          onclick={() => addWindow(list)}>Zeitfenster hinzufügen</button
        >
      </div>
    {/if}
  </fieldset>
{/snippet}

<div class="hems">
  <div class="section-head">
    <h2>Energiemanagement</h2>
    {#if status}
      <StatusPill
        label={`Planer: ${PLANNER_MODE_LABELS[status.mode]}`}
        tone={status.mode === 'off' ? 'neutral' : status.mode === 'control' ? 'ok' : 'warn'}
      />
    {/if}
  </div>

  {#if loadError}
    <p class="banner error" role="alert">{loadError}</p>
  {:else if status === null || draft === null}
    <p class="secondary" role="status">Einstellungen werden geladen …</p>
  {:else}
    <section class="card" aria-labelledby="hems-stage-title">
      <h3 id="hems-stage-title">Planer</h3>
      <p class="secondary">
        Der Planer legt das Warmwasser-Laden in Stunden mit PV-Überschuss oder günstigem Strom und heizt die Zonen dann
        vor. Er ändert nur Solltemperaturen und die Einmalladung, nie Betriebsarten, und kehrt danach zum Grundwert zurück.
        Ändern Sie eine Solltemperatur selbst, wird sie zum neuen Grundwert.
      </p>
      <div class="segments stage" role="group" aria-label="Stufe des Planers">
        {#each MODES as mode (mode)}
          <button
            type="button"
            class:active={status.mode === mode}
            aria-pressed={status.mode === mode}
            disabled={switching}
            onclick={() => requestMode(mode)}>{PLANNER_MODE_LABELS[mode]}</button
          >
        {/each}
      </div>
      <p class="hint">{PLANNER_MODE_HINTS[status.mode]}</p>
      {#if confirmingControl}
        <div class="banner warn confirm" role="alertdialog" aria-label="Steuern bestätigen">
          <p>{CONTROL_CONFIRMATION}</p>
          <div class="buttons">
            <button type="button" class="button primary" disabled={switching} onclick={() => switchMode('control')}
              >Steuern einschalten</button
            >
            <button type="button" class="button" onclick={() => (confirmingControl = false)}>Abbrechen</button>
          </div>
        </div>
      {/if}
      {#if modeError}
        <p class="error" role="alert">{modeError}</p>
      {/if}
      {#if status.mode !== 'off' && status.problems.length > 0}
        <ul class="problems" aria-label="Fehlende Daten">
          {#each status.problems as problem (problem)}
            <li>{problem}</li>
          {/each}
        </ul>
      {/if}
    </section>

    <div class="pair">
      <section class="card" aria-labelledby="hems-tariff-title">
        <h3 id="hems-tariff-title">Strompreis</h3>
        <label class="field">
          <span class="field-label">Tarif</span>
          <select bind:value={draft.tariff.kind} onchange={edited}>
            {#each TARIFFS as kind (kind)}
              <option value={kind}>{TARIFF_LABELS[kind]}</option>
            {/each}
          </select>
        </label>
        {#if draft.tariff.kind === 'fixed'}
          <label class="field">
            <span class="field-label">Arbeitspreis (ct/kWh)</span>
            <input type="number" min="0" max="200" step="0.01" bind:value={draft.tariff.price_ct} oninput={edited} />
          </label>
        {:else if draft.tariff.kind === 'dynamic'}
          {@render entity('Preis-Sensor (epex_spot)', 'price', draft.tariff.price_entity, (value) => {
            if (draft) draft.tariff.price_entity = value;
          })}
          <p class="hint">
            Der Sensor „Total Price“ von epex_spot enthält Aufschläge und MwSt. schon, wenn sie in der Integration
            eingetragen sind; dann hier 0 lassen.
          </p>
          <div class="numbers">
            <label class="field">
              <span class="field-label">Aufschlag (ct/kWh)</span>
              <input type="number" min="0" max="200" step="0.01" bind:value={draft.tariff.surcharge_ct} oninput={edited} />
            </label>
            <label class="field">
              <span class="field-label">MwSt. (%)</span>
              <input type="number" min="0" max="50" step="1" bind:value={draft.tariff.vat_percent} oninput={edited} />
            </label>
          </div>
        {:else}
          <div class="numbers">
            <label class="field">
              <span class="field-label">Standardzeit ST (ct/kWh)</span>
              <input type="number" min="0" max="200" step="0.01" bind:value={draft.tariff.price_ct} oninput={edited} />
            </label>
            <label class="field">
              <span class="field-label">Hochtarif HT (ct/kWh)</span>
              <input type="number" min="0" max="200" step="0.01" bind:value={draft.tariff.high_price_ct} oninput={edited} />
            </label>
            <label class="field">
              <span class="field-label">Niedertarif NT (ct/kWh)</span>
              <input type="number" min="0" max="200" step="0.01" bind:value={draft.tariff.low_price_ct} oninput={edited} />
            </label>
          </div>
          <p class="hint">Preise jeweils mit Netzentgelt. Die Zeitfenster stehen auf der Seite Ihres Netzbetreibers.</p>
          {@render windows('Zeitfenster Hochtarif (HT)', draft.tariff.high_windows)}
          {@render windows('Zeitfenster Niedertarif (NT)', draft.tariff.low_windows)}
        {/if}
        <label class="field">
          <span class="field-label">Einspeisevergütung (ct/kWh)</span>
          <input type="number" min="0" max="100" step="0.01" bind:value={draft.tariff.feed_in_ct} oninput={edited} />
        </label>
      </section>

      <section class="card" aria-labelledby="hems-sources-title">
        <h3 id="hems-sources-title">PV, Hausverbrauch und Akku</h3>
        {@render entity('PV-Prognose heute', 'pv_forecast', draft.pv_forecast_today, (value) => {
          if (draft) draft.pv_forecast_today = value;
        })}
        {@render entity('PV-Prognose morgen', 'pv_forecast', draft.pv_forecast_tomorrow, (value) => {
          if (draft) draft.pv_forecast_tomorrow = value;
        })}
        {@render entity('Hausverbrauch (W oder kW, mit Wärmepumpe)', 'power', draft.house_power, (value) => {
          if (draft) draft.house_power = value;
        })}
        {@render entity('Akku-Ladestand (%)', 'battery', draft.battery_soc, (value) => {
          if (draft) draft.battery_soc = value;
        })}
        {#if draft.battery_soc}
          <div class="numbers">
            <label class="field">
              <span class="field-label">Kapazität (kWh)</span>
              <input type="number" min="0" max="200" step="0.1" bind:value={draft.battery_capacity_kwh} oninput={edited} />
            </label>
            <label class="field">
              <span class="field-label">Ladeleistung (kW)</span>
              <input type="number" min="0" max="50" step="0.1" bind:value={draft.battery_charge_kw} oninput={edited} />
            </label>
            <label class="field">
              <span class="field-label">Entladegrenze (%)</span>
              <input type="number" min="0" max="100" step="1" bind:value={draft.battery_min_soc} oninput={edited} />
            </label>
          </div>
        {/if}
        <p class="hint">
          Als Überschuss zählt, was nach dem Hausverbrauch und dem Laden des Akkus ins Netz ginge. Den üblichen
          Hausverbrauch lernt der Planer aus den letzten sieben Tagen.
        </p>
      </section>
    </div>

    <div class="pair">
      <section class="card" aria-labelledby="hems-weather-title">
        <h3 id="hems-weather-title">Temperaturprognose</h3>
        <fieldset class="choice">
          <legend class="sr-only">Quelle</legend>
          <label class="radio">
            <input type="radio" value="home_assistant" bind:group={draft.weather_source} onchange={edited} />
            <span>Wetter-Entität aus Home Assistant</span>
          </label>
          <label class="radio">
            <input type="radio" value="open_meteo" bind:group={draft.weather_source} onchange={edited} />
            <span>Open-Meteo mit eigenem Standort</span>
          </label>
        </fieldset>
        {#if draft.weather_source === 'home_assistant'}
          {@render entity('Wetter-Entität', 'weather', draft.weather_entity, (value) => {
            if (draft) draft.weather_entity = value;
          })}
        {:else}
          <div class="numbers">
            <label class="field">
              <span class="field-label">Breite</span>
              <input type="number" min="-90" max="90" step="0.0001" bind:value={draft.latitude} oninput={edited} />
            </label>
            <label class="field">
              <span class="field-label">Länge</span>
              <input type="number" min="-180" max="180" step="0.0001" bind:value={draft.longitude} oninput={edited} />
            </label>
          </div>
          {#if homeAssistantAvailable}
            <div><button type="button" class="button small" onclick={takeLocation}>Standort aus Home Assistant übernehmen</button></div>
          {/if}
          {#if locationError}
            <p class="error" role="alert">{locationError}</p>
          {/if}
          <p class="hint">
            Die App fragt dafür höchstens einmal pro Stunde api.open-meteo.com ab – ihre einzige Verbindung ins Internet.
          </p>
        {/if}
      </section>

      <section class="card" aria-labelledby="hems-limits-title">
        <h3 id="hems-limits-title">Grenzen</h3>
        {#each zones as zone (zone.id)}
          {@const current = limits(zone.id)}
          <div class="numbers">
            <label class="field">
              <span class="field-label">{zone.label}: höchstens hoch</span>
              <select value={current.up_k} onchange={(event) => setLimit(zone.id, 'up_k', Number(event.currentTarget.value))}>
                {#each SHIFTS as shift (shift)}
                  <option value={shift}>{shiftLabel('+', shift)}</option>
                {/each}
              </select>
            </label>
            <label class="field">
              <span class="field-label">{zone.label}: höchstens runter</span>
              <select
                value={current.down_k}
                onchange={(event) => setLimit(zone.id, 'down_k', Number(event.currentTarget.value))}
              >
                {#each SHIFTS as shift (shift)}
                  <option value={shift}>{shiftLabel('−', shift)}</option>
                {/each}
              </select>
            </label>
          </div>
        {:else}
          <p class="hint">Die Grenzen der Zonen lassen sich einstellen, sobald die Wärmepumpe verbunden ist.</p>
        {/each}
        <div class="numbers">
          <label class="field">
            <span class="field-label">Warmwasser höchstens (°C)</span>
            <input type="number" min="35" max="70" step="1" bind:value={draft.dhw_max_c} oninput={edited} />
          </label>
          <label class="field">
            <span class="field-label">Heizgrenze (°C außen)</span>
            <input type="number" min="0" max="25" step="0.5" bind:value={draft.heating_limit_c} oninput={edited} />
          </label>
        </div>
        <p class="hint">
          Warmwasser wird nur angehoben, nie abgesenkt. Liegt die erwartete Außentemperatur über der Heizgrenze, bleiben die
          Zonen unverändert.
        </p>
      </section>
    </div>

    <div class="actions">
      <button type="button" class="button primary" disabled={!changed || !complete || saving} onclick={save}>
        Speichern
      </button>
      {#if saveError}
        <p class="error" role="alert">{saveError}</p>
      {:else if !complete}
        <p class="hint">Bitte alle Zahlenfelder ausfüllen.</p>
      {:else if justSaved && !changed}
        <p class="hint" role="status">Gespeichert. Der Planer plant neu.</p>
      {/if}
    </div>
  {/if}
</div>

<style>
  .hems {
    display: flex;
    flex-direction: column;
    gap: 24px;
  }

  .section-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
    margin-top: 8px;
  }

  .section-head h2 {
    font-size: 20px;
    font-weight: 600;
  }

  .card h3 {
    font-size: 18px;
    font-weight: 600;
  }

  .stage {
    max-width: 420px;
  }

  .stage > button {
    flex: 1 1 0;
  }

  .stage > button:disabled {
    cursor: wait;
  }

  .confirm {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .buttons {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
  }

  .problems {
    margin: 0;
    padding: 12px 16px 12px 32px;
    border-radius: 12px;
    background: var(--warn-bg);
    color: var(--warn-text);
    font-size: 14px;
  }

  .pair {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    gap: 24px;
    align-items: start;
  }

  .field,
  .entity-field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .field-label,
  legend {
    font-size: 13px;
    color: var(--text-secondary);
  }

  .numbers {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
  }

  input[type='number'] {
    width: 100%;
  }

  .row-end {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }

  code {
    font-size: 13px;
    color: var(--text-hint);
    word-break: break-all;
  }

  .button.small {
    min-height: 44px;
    padding: 0 14px;
    font-size: 14px;
  }

  fieldset {
    margin: 0;
    padding: 0;
    border: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .window {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 12px;
  }

  .window label {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 14px;
  }

  .radio {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 44px;
    cursor: pointer;
  }

  .radio input {
    width: 20px;
    height: 20px;
  }

  .actions {
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
  }

  .error {
    color: var(--error-text);
    font-size: 14px;
  }

  @media (max-width: 420px) {
    .pair {
      grid-template-columns: 1fr;
    }
  }
</style>
