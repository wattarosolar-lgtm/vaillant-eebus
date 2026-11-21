<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiError, getHems, getLocation, saveHemsSettings, setHemsMode } from '../lib/api';
  import { PLANNER_MODE_HINTS, PLANNER_MODE_LABELS, WEATHER_SOURCE_LABELS, ZONE_KIND_LABELS } from '../lib/texts';
  import type { EntityKind, HemsSettings, HemsStatus, PlannerMode, WeatherSource, ZoneConfig, ZoneKind } from '../lib/types';
  import { guardUnsaved } from '../lib/unsaved';
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
  const SOURCES: WeatherSource[] = ['open_meteo', 'home_assistant'];
  const KINDS: ZoneKind[] = ['floor', 'radiator'];
  const BANDS = [0, 0.5, 1, 1.5, 2, 2.5, 3];

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
      [
        draft.step_k,
        draft.min_interval_min,
        draft.night_loss_k,
        draft.defrost_loss_k,
        draft.target_starts,
        draft.heating_limit_c,
        draft.block_above_c,
        draft.cloud_commands_max,
        draft.zone1_threshold_w,
      ].every((value) => typeof value === 'number' && Number.isFinite(value)) &&
      draft.night_start !== '' &&
      draft.night_end !== '',
  );

  onMount(() => guardUnsaved(() => changed));

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

  async function switchMode(mode: PlannerMode): Promise<void> {
    if (status === null || mode === status.mode || !status.available_modes.includes(mode)) {
      return;
    }
    switching = true;
    modeError = null;
    try {
      // Only the stage changes; unsaved settings stay in the form.
      status = await setHemsMode(mode);
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

  function zoneConfig(zoneId: string): ZoneConfig {
    return (
      draft?.zones.find((zone) => zone.zone_id === zoneId) ?? {
        zone_id: zoneId,
        kind: null,
        flow_entity: null,
        flow_setpoint_entity: null,
        design_flow_c: null,
        design_return_c: null,
        band_down_k: null,
        band_up_k: null,
      }
    );
  }

  function setZone<K extends keyof ZoneConfig>(zoneId: string, key: K, value: ZoneConfig[K]): void {
    if (draft === null) {
      return;
    }
    const next = { ...zoneConfig(zoneId), [key]: value };
    const others = draft.zones.filter((zone) => zone.zone_id !== zoneId);
    draft.zones = [...others, next].sort((a, b) => a.zone_id.localeCompare(b.zone_id));
    edited();
  }

  /** An empty number field means "not set" for optional values. */
  function optional(value: string): number | null {
    if (value.trim() === '') {
      return null;
    }
    const number = Number(value.replace(',', '.'));
    return Number.isFinite(number) ? number : null;
  }

  function bandLabel(sign: string, value: number | null): string {
    return value === null ? 'Vorgabe' : `${value === 0 ? '' : sign}${String(value).replace('.', ',')} K`;
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

<div class="hems">
  <div class="section-head">
    <h2>Energiemanagement</h2>
    {#if status}
      <StatusPill
        label={`Stufe: ${PLANNER_MODE_LABELS[status.mode]}`}
        tone={status.mode === 'off' ? 'neutral' : 'warn'}
      />
    {/if}
  </div>

  {#if loadError}
    <p class="banner error" role="alert">{loadError}</p>
  {:else if status === null || draft === null}
    <p class="secondary" role="status">Einstellungen werden geladen …</p>
  {:else}
    <section class="card" aria-labelledby="hems-stage-title">
      <h3 id="hems-stage-title">Stufe</h3>
      <p class="secondary">
        Das Energiemanagement beobachtet die Wärmepumpe und die Räume, lernt Haus und Räume kennen und plant 48 Stunden
        voraus: Vorlauf, Vorausschau, Abtauungen, Nachtabsenkung und Takten. Für die Zonen zeigt es in dieser Version
        nur, was es tun würde, und sendet nichts. Die Heizkörperthermostate stellt die Raumregelung (Seite Räume).
      </p>
      <div class="segments stage" role="group" aria-label="Stufe des Energiemanagements">
        {#each MODES as mode (mode)}
          {@const available = status.available_modes.includes(mode)}
          <button
            type="button"
            class:active={status.mode === mode}
            aria-pressed={status.mode === mode}
            disabled={switching || !available}
            title={available ? undefined : PLANNER_MODE_HINTS[mode]}
            onclick={() => void switchMode(mode)}>{PLANNER_MODE_LABELS[mode]}{available ? '' : ' (ab 1.5.0)'}</button
          >
        {/each}
      </div>
      <p class="hint">{PLANNER_MODE_HINTS[status.mode]}</p>
      {#if modeError}
        <p class="error" role="alert">{modeError}</p>
      {/if}
      {#if status.problems.length > 0}
        <ul class="problems" aria-label="Fehlende Daten">
          {#each status.problems as problem (problem)}
            <li>{problem}</li>
          {/each}
        </ul>
      {/if}
    </section>

    <div class="pair">
      <section class="card" aria-labelledby="hems-weather-title">
        <h3 id="hems-weather-title">Wetterprognose</h3>
        <fieldset class="choice">
          <legend class="sr-only">Quelle</legend>
          {#each SOURCES as source (source)}
            <label class="radio">
              <input type="radio" value={source} bind:group={draft.weather_source} onchange={edited} />
              <span>{WEATHER_SOURCE_LABELS[source]}</span>
            </label>
          {/each}
        </fieldset>
        <div class="numbers">
          <label class="field">
            <span class="field-label">Breite</span>
            <input type="number" min="-90" max="90" step="0.01" bind:value={draft.latitude} oninput={edited} />
          </label>
          <label class="field">
            <span class="field-label">Länge</span>
            <input type="number" min="-180" max="180" step="0.01" bind:value={draft.longitude} oninput={edited} />
          </label>
        </div>
        {#if homeAssistantAvailable}
          <div>
            <button type="button" class="button small" onclick={takeLocation}>Standort aus Home Assistant übernehmen</button>
          </div>
        {/if}
        {#if locationError}
          <p class="error" role="alert">{locationError}</p>
        {/if}
        {@render entity('Wetter-Entität (Rückfall)', 'weather', draft.weather_entity, (value) => {
          if (draft) draft.weather_entity = value;
        })}
        <p class="hint">
          Open-Meteo wird höchstens einmal pro Stunde gefragt, mit auf etwa 1 km gerundetem Standort (api.open-meteo.com).
          Antwortet es nicht, nimmt die App die Wetter-Entität. Die Prognose wird am Außenfühler der Wärmepumpe geeicht.
        </p>
      </section>

      <section class="card" aria-labelledby="hems-learn-title">
        <h3 id="hems-learn-title">Lernen</h3>
        <p class="secondary">
          Aus der Langzeitstatistik von Home Assistant lernt die App, wie viel Wärme das Haus braucht. Wählen Sie dafür die
          Wärmesensoren der myVAILLANT-Integration (Wärme heute).
        </p>
        {@render entity('Wärme Heizen', 'energy', draft.heat_heating_entity, (value) => {
          if (draft) draft.heat_heating_entity = value;
        })}
        {@render entity('Wärme Warmwasser', 'energy', draft.heat_dhw_entity, (value) => {
          if (draft) draft.heat_dhw_entity = value;
        })}
        {@render entity('Puffertemperatur', 'temperature', draft.buffer_entity, (value) => {
          if (draft) draft.buffer_entity = value;
        })}
        <div class="numbers">
          <label class="field">
            <span class="field-label">Normaußentemperatur (°C)</span>
            <input
              type="number"
              min="-30"
              max="10"
              step="0.1"
              value={draft.design_outdoor_c ?? ''}
              oninput={(event) => {
                if (draft) draft.design_outdoor_c = optional(event.currentTarget.value);
                edited();
              }}
            />
          </label>
          <label class="field">
            <span class="field-label">Heizlast des Hauses (W)</span>
            <input
              type="number"
              min="100"
              max="50000"
              step="10"
              value={draft.heat_load_w ?? ''}
              oninput={(event) => {
                if (draft) draft.heat_load_w = optional(event.currentTarget.value);
                edited();
              }}
            />
          </label>
        </div>
        <p class="hint">Aus dem statischen hydraulischen Abgleich; nur Startwert, bis die App genug gelernt hat.</p>
      </section>
    </div>

    <section class="card" aria-labelledby="hems-zones-title">
      <h3 id="hems-zones-title">Zonen</h3>
      {#each zones as zone (zone.id)}
        {@const config = zoneConfig(zone.id)}
        <fieldset class="zone">
          <legend>{zone.label}</legend>
          <div class="numbers">
            <label class="field">
              <span class="field-label">Art</span>
              <select
                value={config.kind ?? ''}
                onchange={(event) =>
                  setZone(zone.id, 'kind', event.currentTarget.value === '' ? null : (event.currentTarget.value as ZoneKind))}
              >
                <option value="">nicht angegeben</option>
                {#each KINDS as kind (kind)}
                  <option value={kind}>{ZONE_KIND_LABELS[kind]}</option>
                {/each}
              </select>
            </label>
            <label class="field">
              <span class="field-label">Auslegung Vorlauf (°C)</span>
              <input
                type="number"
                min="20"
                max="80"
                step="0.5"
                value={config.design_flow_c ?? ''}
                oninput={(event) => setZone(zone.id, 'design_flow_c', optional(event.currentTarget.value))}
              />
            </label>
            <label class="field">
              <span class="field-label">Auslegung Rücklauf (°C)</span>
              <input
                type="number"
                min="20"
                max="80"
                step="0.5"
                value={config.design_return_c ?? ''}
                oninput={(event) => setZone(zone.id, 'design_return_c', optional(event.currentTarget.value))}
              />
            </label>
            <label class="field">
              <span class="field-label">Band nach unten</span>
              <select
                value={config.band_down_k === null ? '' : String(config.band_down_k)}
                onchange={(event) => setZone(zone.id, 'band_down_k', optional(event.currentTarget.value))}
              >
                <option value="">Vorgabe</option>
                {#each BANDS as band (band)}
                  <option value={String(band)}>{bandLabel('−', band)}</option>
                {/each}
              </select>
            </label>
            <label class="field">
              <span class="field-label">Band nach oben</span>
              <select
                value={config.band_up_k === null ? '' : String(config.band_up_k)}
                onchange={(event) => setZone(zone.id, 'band_up_k', optional(event.currentTarget.value))}
              >
                <option value="">Vorgabe</option>
                {#each BANDS as band (band)}
                  <option value={String(band)}>{bandLabel('+', band)}</option>
                {/each}
              </select>
            </label>
          </div>
          {@render entity('Vorlauf-Ist', 'temperature', config.flow_entity, (value) => setZone(zone.id, 'flow_entity', value))}
          {@render entity('Vorlauf-Soll', 'temperature', config.flow_setpoint_entity, (value) =>
            setZone(zone.id, 'flow_setpoint_entity', value),
          )}
        </fieldset>
      {:else}
        <p class="hint">Die Zonen lassen sich einstellen, sobald die Wärmepumpe verbunden ist.</p>
      {/each}
      <p class="hint">
        Die Fußbodenheizung dient als Speicher (Blockbetrieb, Zone-1-Logik), die Heizkörper-Zone bekommt den Vorlauf nach
        den Ventilstellungen. Vorgabe für das Band: Fußbodenheizung −1 … +2 K, Heizkörper −2 … +2 K. Bei myVAILLANT heißen
        die Vorlauf-Sensoren meist „… circuit_0/1 … flow temperature“.
      </p>
    </section>

    <div class="pair">
      <section class="card" aria-labelledby="hems-night-title">
        <h3 id="hems-night-title">Nachtabsenkung und Abtauen</h3>
        <div class="numbers">
          <label class="field">
            <span class="field-label">Nachtfenster von</span>
            <input type="time" bind:value={draft.night_start} oninput={edited} />
          </label>
          <label class="field">
            <span class="field-label">bis</span>
            <input type="time" bind:value={draft.night_end} oninput={edited} />
          </label>
          <label class="field">
            <span class="field-label">Verlust nachts höchstens (K)</span>
            <input type="number" min="0" max="3" step="0.1" bind:value={draft.night_loss_k} oninput={edited} />
          </label>
          <label class="field">
            <span class="field-label">Verlust gegen Abtauen höchstens (K)</span>
            <input type="number" min="0" max="3" step="0.1" bind:value={draft.defrost_loss_k} oninput={edited} />
          </label>
        </div>
        <p class="hint">
          Vorgabe für alle Räume; einzelne Räume stellen Sie auf der Seite „Räume“ anders ein. Die Absenkung nutzt die App
          nur, wenn sich das Wiederaufheizen lohnt, und nie in feuchten Räumen.
        </p>
      </section>

      <section class="card" aria-labelledby="hems-cycles-title">
        <h3 id="hems-cycles-title">Takten und Grenzen</h3>
        <div class="numbers">
          <label class="field">
            <span class="field-label">Ziel: Starts je Tag höchstens</span>
            <input type="number" min="1" max="48" step="1" bind:value={draft.target_starts} oninput={edited} />
          </label>
          <label class="field">
            <span class="field-label">Blockbetrieb ab (°C außen)</span>
            <input type="number" min="-10" max="20" step="0.5" bind:value={draft.block_above_c} oninput={edited} />
          </label>
          <label class="field">
            <span class="field-label">Heizgrenze (°C Tagesmittel)</span>
            <input type="number" min="0" max="25" step="0.5" bind:value={draft.heating_limit_c} oninput={edited} />
          </label>
          <label class="field">
            <span class="field-label">Zone 1 aus unter (W)</span>
            <input type="number" min="5" max="500" step="1" bind:value={draft.zone1_threshold_w} oninput={edited} />
          </label>
        </div>
        <h4>Quick-Veto (wirkt ab 1.5.0)</h4>
        <div class="numbers">
          <label class="field">
            <span class="field-label">Schritt (K)</span>
            <select bind:value={draft.step_k} onchange={edited}>
              <option value={0.5}>0,5 K</option>
              <option value={1}>1 K</option>
            </select>
          </label>
          <label class="field">
            <span class="field-label">Abstand je Zone (min)</span>
            <input type="number" min="15" max="120" step="5" bind:value={draft.min_interval_min} oninput={edited} />
          </label>
          <label class="field">
            <span class="field-label">Cloud-Befehle je Tag höchstens</span>
            <input type="number" min="10" max="1000" step="1" bind:value={draft.cloud_commands_max} oninput={edited} />
          </label>
        </div>
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
        <p class="hint" role="status">Gespeichert. Das Energiemanagement plant neu.</p>
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

  h4 {
    margin: 4px 0 0;
    font-size: 15px;
    font-weight: 600;
  }

  .stage {
    max-width: 520px;
  }

  .stage > button {
    flex: 1 1 0;
  }

  .stage > button:disabled {
    cursor: not-allowed;
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
    min-width: 0;
  }

  .field-label,
  legend {
    font-size: 13px;
    color: var(--text-secondary);
  }

  .zone legend {
    font-size: 15px;
    font-weight: 600;
    color: var(--text);
    margin-bottom: 4px;
  }

  .numbers {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
  }

  input[type='number'],
  input[type='time'],
  select {
    width: 100%;
    box-sizing: border-box;
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
    min-width: 0;
  }

  .zone {
    padding-bottom: 16px;
    border-bottom: 1px solid var(--divider);
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
