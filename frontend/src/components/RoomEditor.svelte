<script lang="ts">
  import { onMount, untrack } from 'svelte';

  import { ApiError, saveRoom } from '../lib/api';
  import type { EntityKind, HeatingSurface, RatedBasis, RoomDetails, RoomsView, SurfaceKind } from '../lib/types';
  import { guardUnsaved } from '../lib/unsaved';
  import EntityPicker from './EntityPicker.svelte';

  let {
    entityId,
    name,
    details,
    homeAssistantAvailable,
    onSaved,
    onCancel,
  }: {
    entityId: string;
    name: string;
    details: RoomDetails;
    homeAssistantAvailable: boolean;
    onSaved: (view: RoomsView) => void;
    onCancel: () => void;
  } = $props();

  const SURFACES_MAX = 4;
  const SCHEDULE_MAX = 4;
  const KINDS: { value: SurfaceKind; label: string }[] = [
    { value: 'radiator', label: 'Heizkörper' },
    { value: 'floor', label: 'Fußbodenheizung' },
  ];
  const BASES: RatedBasis[] = ['75/65', '55/45', '50/40'];

  // The editor starts from the room's details when it opens; later updates of the list do not reset it.
  const initial = untrack(() => JSON.stringify(details));
  let draft = $state<RoomDetails>(JSON.parse(initial) as RoomDetails);
  let names = $state<Record<string, string>>({});
  let saving = $state(false);
  let error = $state<string | null>(null);

  const changed = $derived(JSON.stringify(draft) !== initial);
  const complete = $derived(
    [draft.setpoint_c, draft.comfort_down_k, draft.comfort_up_k].every(
      (value) => typeof value === 'number' && Number.isFinite(value),
    ) && draft.schedule.every((window) => window.start !== '' && window.end !== '' && Number.isFinite(window.setpoint_c)),
  );

  onMount(() => guardUnsaved(() => changed));

  function optional(value: string): number | null {
    if (value.trim() === '') {
      return null;
    }
    const number = Number(value.replace(',', '.'));
    return Number.isFinite(number) ? number : null;
  }

  function addSurface(): void {
    const surface: HeatingSurface = { kind: 'radiator', rated_w: null, rated_basis: '75/65', flow_lph: null, presetting: '' };
    draft.surfaces = [...draft.surfaces, surface];
  }

  function addWindow(): void {
    draft.schedule = [...draft.schedule, { start: '06:00', end: '22:00', setpoint_c: draft.setpoint_c }];
  }

  async function save(): Promise<void> {
    saving = true;
    error = null;
    try {
      onSaved(await saveRoom(entityId, draft));
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Die Raumdaten konnten nicht gespeichert werden.';
    } finally {
      saving = false;
    }
  }
</script>

{#snippet entity(label: string, kind: EntityKind, value: string | null, set: (value: string | null) => void)}
  <div class="field">
    <span class="field-label">{label}</span>
    {#if value}
      <div class="row-end">
        <span>{#if names[value]}{names[value]} {/if}<code>{value}</code></span>
        <button type="button" class="button small" aria-label={`${label} entfernen`} onclick={() => set(null)}>Entfernen</button>
      </div>
    {:else if homeAssistantAvailable}
      <EntityPicker
        {kind}
        label={`${label} wählen`}
        onPick={(choice) => {
          names[choice.entity_id] = choice.name;
          set(choice.entity_id);
        }}
      />
    {:else}
      <p class="hint">Die Auswahl gibt es nur, wenn die App in Home Assistant läuft.</p>
    {/if}
  </div>
{/snippet}

<form
  class="editor"
  aria-label={`Raumdaten ${name}`}
  onsubmit={(event) => {
    event.preventDefault();
    void save();
  }}
>
  <fieldset>
    <legend>Soll und Zeitplan</legend>
    <div class="numbers">
      <label class="field">
        <span class="field-label">Raum-Soll (°C)</span>
        <input type="number" min="12" max="26" step="0.5" bind:value={draft.setpoint_c} />
      </label>
      <label class="field">
        <span class="field-label">Komfortband unten (K)</span>
        <input type="number" min="0" max="2" step="0.1" bind:value={draft.comfort_down_k} />
      </label>
      <label class="field">
        <span class="field-label">Komfortband oben (K)</span>
        <input type="number" min="0" max="2" step="0.1" bind:value={draft.comfort_up_k} />
      </label>
    </div>
    {#each draft.schedule as window, index (index)}
      <div class="window">
        <label>von <input type="time" bind:value={window.start} /></label>
        <label>bis <input type="time" bind:value={window.end} /></label>
        <label>Soll <input type="number" min="12" max="26" step="0.5" bind:value={window.setpoint_c} /> °C</label>
        <button
          type="button"
          class="button small"
          aria-label={`Zeitfenster ${index + 1} entfernen`}
          onclick={() => (draft.schedule = draft.schedule.filter((_, other) => other !== index))}>Entfernen</button
        >
      </div>
    {/each}
    {#if draft.schedule.length < SCHEDULE_MAX}
      <div><button type="button" class="button small" onclick={addWindow}>Zeitfenster hinzufügen</button></div>
    {/if}
    <p class="hint">
      Außerhalb der Zeitfenster gilt das Raum-Soll, jeden Tag gleich. In dieser Version nutzt nur die Vorschau diese
      Werte; die Thermostate stellt die App erst ab 1.5.0.
    </p>
  </fieldset>

  <fieldset>
    <legend>Absenkung und Schutz</legend>
    <div class="numbers">
      <label class="field">
        <span class="field-label">Verlust nachts höchstens (K)</span>
        <input
          type="number"
          min="0"
          max="3"
          step="0.1"
          placeholder="Vorgabe"
          value={draft.night_loss_k ?? ''}
          oninput={(event) => (draft.night_loss_k = optional(event.currentTarget.value))}
        />
      </label>
      <label class="field">
        <span class="field-label">Verlust gegen Abtauen höchstens (K)</span>
        <input
          type="number"
          min="0"
          max="3"
          step="0.1"
          placeholder="Vorgabe"
          value={draft.defrost_loss_k ?? ''}
          oninput={(event) => (draft.defrost_loss_k = optional(event.currentTarget.value))}
        />
      </label>
      <label class="field">
        <span class="field-label">Notfallwert (°C)</span>
        <input
          type="number"
          min="5"
          max="26"
          step="0.5"
          value={draft.emergency_c ?? ''}
          oninput={(event) => (draft.emergency_c = optional(event.currentTarget.value))}
        />
      </label>
    </div>
    <label class="check">
      <input type="checkbox" bind:checked={draft.throttle} />
      <span>Darf gedrosselt werden, damit ein kalter Raum mehr Wasser bekommt (ab 1.5.0)</span>
    </label>
  </fieldset>

  <fieldset>
    <legend>Thermostat und Sensoren</legend>
    {@render entity('Heizkörperthermostat', 'thermostat', draft.trv_entity, (value) => (draft.trv_entity = value))}
    {@render entity('Ventilstellung (%)', 'percent', draft.valve_entity, (value) => (draft.valve_entity = value))}
    {@render entity('Luftfeuchte (%)', 'humidity', draft.humidity_entity, (value) => (draft.humidity_entity = value))}
    <p class="hint">
      Aqara W600: Sensor „position“; Eurotronic Comet: „pi_heating_demand“. Die Feuchte dient nur der Fenstererkennung und
      dem Schutz vor Schimmel bei der Nachtabsenkung.
    </p>
  </fieldset>

  <fieldset>
    <legend>Statischer Abgleich</legend>
    <div class="numbers">
      <label class="field">
        <span class="field-label">Heizlast (W)</span>
        <input
          type="number"
          min="0"
          max="10000"
          step="1"
          value={draft.heat_load_w ?? ''}
          oninput={(event) => (draft.heat_load_w = optional(event.currentTarget.value))}
        />
      </label>
      <label class="field">
        <span class="field-label">Auslegungs-Soll (°C)</span>
        <input
          type="number"
          min="10"
          max="30"
          step="0.5"
          value={draft.design_c ?? ''}
          oninput={(event) => (draft.design_c = optional(event.currentTarget.value))}
        />
      </label>
    </div>
    {#each draft.surfaces as surface, index (index)}
      <div class="surface">
        <span class="field-label">Heizfläche {index + 1}</span>
        <div class="numbers">
          <label class="field">
            <span class="field-label">Art</span>
            <select bind:value={surface.kind}>
              {#each KINDS as kind (kind.value)}
                <option value={kind.value}>{kind.label}</option>
              {/each}
            </select>
          </label>
          <label class="field">
            <span class="field-label">Normleistung (W)</span>
            <input
              type="number"
              min="0"
              max="20000"
              step="1"
              value={surface.rated_w ?? ''}
              oninput={(event) => (surface.rated_w = optional(event.currentTarget.value))}
            />
          </label>
          <label class="field">
            <span class="field-label">Bezug</span>
            <select bind:value={surface.rated_basis}>
              {#each BASES as basis (basis)}
                <option value={basis}>{basis}</option>
              {/each}
            </select>
          </label>
          <label class="field">
            <span class="field-label">Durchfluss (l/h)</span>
            <input
              type="number"
              min="0"
              max="2000"
              step="1"
              value={surface.flow_lph ?? ''}
              oninput={(event) => (surface.flow_lph = optional(event.currentTarget.value))}
            />
          </label>
          <label class="field">
            <span class="field-label">Voreinstellung</span>
            <input type="text" maxlength="20" bind:value={surface.presetting} />
          </label>
        </div>
        <div>
          <button
            type="button"
            class="button small"
            aria-label={`Heizfläche ${index + 1} entfernen`}
            onclick={() => (draft.surfaces = draft.surfaces.filter((_, other) => other !== index))}>Entfernen</button
          >
        </div>
      </div>
    {/each}
    {#if draft.surfaces.length < SURFACES_MAX}
      <div><button type="button" class="button small" onclick={addSurface}>Heizfläche hinzufügen</button></div>
    {/if}
    <p class="hint">Werte aus dem Gutachten; nur Startwerte. Die App lernt die echten aus dem Betrieb.</p>
  </fieldset>

  <div class="actions">
    <button type="submit" class="button primary" disabled={!changed || !complete || saving}>Speichern</button>
    <button type="button" class="button" onclick={onCancel}>Abbrechen</button>
    {#if error}
      <p class="error" role="alert">{error}</p>
    {:else if !complete}
      <p class="hint">Bitte Raum-Soll, Komfortband und Zeitfenster ausfüllen.</p>
    {/if}
  </div>
</form>

<style>
  .editor {
    display: flex;
    flex-direction: column;
    gap: 20px;
    padding-top: 8px;
    border-top: 1px solid var(--divider);
  }

  fieldset {
    margin: 0;
    padding: 0;
    border: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-width: 0;
  }

  legend {
    font-size: 15px;
    font-weight: 600;
    margin-bottom: 6px;
  }

  .numbers {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 12px;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }

  .field-label {
    font-size: 13px;
    color: var(--text-secondary);
  }

  input[type='number'],
  input[type='text'],
  select {
    width: 100%;
    box-sizing: border-box;
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

  .window input[type='number'] {
    width: 80px;
  }

  .surface {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px;
    border-radius: var(--radius-control);
    background: var(--surface-muted);
  }

  .check {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 44px;
    cursor: pointer;
    font-size: 14px;
  }

  .check input {
    flex: none;
    width: 20px;
    height: 20px;
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

  .actions {
    display: flex;
    align-items: center;
    gap: 12px 16px;
    flex-wrap: wrap;
  }

  .error {
    color: var(--error-text);
    font-size: 14px;
  }
</style>
