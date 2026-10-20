<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiError, getSettings, saveSettings } from '../lib/api';
  import { EXPORT_GROUPS } from '../lib/texts';
  import type { AppSettings, EntityChoice, ExportGroup, HomeAssistantState, Room } from '../lib/types';
  import EntityPicker from './EntityPicker.svelte';
  import StatusPill from './StatusPill.svelte';

  let {
    zones,
    homeAssistant,
  }: {
    zones: { id: string; label: string }[];
    /** Live state of the connection; null until the first update. */
    homeAssistant: HomeAssistantState | null;
  } = $props();

  /** Heat sensors that are added up, e.g. heating and hot water (backend limit). */
  const HEAT_SENSORS_MAX = 4;

  let saved = $state<AppSettings | null>(null);
  let rooms = $state<Room[]>([]);
  let heatEntities = $state<string[]>([]);
  // Friendly names of heat sensors picked in this visit; saved settings only know the IDs.
  let heatNames = $state<Record<string, string>>({});
  let exportGroups = $state<ExportGroup[]>([]);
  let loadError = $state<string | null>(null);
  let saveError = $state<string | null>(null);
  let saving = $state(false);
  let justSaved = $state(false);

  const changed = $derived(
    saved !== null &&
      JSON.stringify([rooms, heatEntities, [...exportGroups].sort()]) !==
        JSON.stringify([saved.rooms, saved.heat_energy_entities, [...saved.export].sort()]),
  );
  const available = $derived(saved?.home_assistant_available ?? false);
  const connection = $derived.by((): { label: string; tone: 'ok' | 'error' | 'neutral' } => {
    if (!available) {
      return { label: 'Nicht verfügbar', tone: 'neutral' };
    }
    if (homeAssistant === null) {
      return { label: 'Wird geprüft …', tone: 'neutral' };
    }
    return homeAssistant.connected
      ? { label: 'Verbunden', tone: 'ok' }
      : { label: 'Nicht erreichbar', tone: 'error' };
  });
  const roomsValid = $derived(rooms.every((room) => room.name.trim().length > 0));

  function adopt(settings: AppSettings): void {
    saved = settings;
    rooms = settings.rooms.map((room) => ({ ...room }));
    heatEntities = [...settings.heat_energy_entities];
    exportGroups = [...settings.export];
  }

  onMount(async () => {
    try {
      adopt(await getSettings());
    } catch (err) {
      loadError = err instanceof ApiError ? err.message : 'Die Einstellungen konnten nicht geladen werden.';
    }
  });

  function zoneLabel(zoneId: string): string {
    return zones.find((zone) => zone.id === zoneId)?.label ?? `Zone ${zoneId}`;
  }

  // Zones the device reports, plus any assigned zone it does not report right now.
  const zoneOptions = $derived([
    ...zones,
    ...rooms
      .map((room) => room.zone_id)
      .filter((zoneId): zoneId is string => zoneId !== null && !zones.some((zone) => zone.id === zoneId))
      .filter((zoneId, index, all) => all.indexOf(zoneId) === index)
      .map((zoneId) => ({ id: zoneId, label: zoneLabel(zoneId) })),
  ]);

  function addRoom(choice: EntityChoice): void {
    rooms = [...rooms, { entity_id: choice.entity_id, name: choice.name.slice(0, 60), zone_id: null }];
    justSaved = false;
  }

  function removeRoom(entityId: string): void {
    rooms = rooms.filter((room) => room.entity_id !== entityId);
    justSaved = false;
  }

  function addHeat(choice: EntityChoice): void {
    heatEntities = [...heatEntities, choice.entity_id];
    heatNames = { ...heatNames, [choice.entity_id]: choice.name };
    justSaved = false;
  }

  function removeHeat(entityId: string): void {
    heatEntities = heatEntities.filter((item) => item !== entityId);
    justSaved = false;
  }

  function toggleGroup(group: ExportGroup, on: boolean): void {
    exportGroups = on ? [...exportGroups, group] : exportGroups.filter((item) => item !== group);
    justSaved = false;
  }

  async function save(): Promise<void> {
    saving = true;
    saveError = null;
    try {
      adopt(
        await saveSettings({
          rooms: rooms.map((room) => ({ ...room, name: room.name.trim() })),
          heat_energy_entities: heatEntities,
          export: exportGroups,
        }),
      );
      justSaved = true;
    } catch (err) {
      saveError = err instanceof ApiError ? err.message : 'Die Einstellungen konnten nicht gespeichert werden.';
    } finally {
      saving = false;
    }
  }
</script>

<div class="home-assistant">
  {#if loadError}
    <p class="banner error" role="alert">{loadError}</p>
  {:else if saved === null}
    <p class="secondary" role="status">Einstellungen werden geladen …</p>
  {:else}
    <div class="section-head">
      <h2>Home Assistant</h2>
      <StatusPill label={connection.label} tone={connection.tone} />
    </div>
    {#if !available}
      <p class="banner info" role="status">
        Home Assistant ist für diese App nicht erreichbar. Räume und Export lassen sich nur einrichten, wenn die App in
        Home Assistant läuft.
      </p>
    {:else if homeAssistant !== null && !homeAssistant.connected}
      <p class="banner warn" role="status">
        Home Assistant antwortet gerade nicht. Die App verbindet sich von selbst neu; bis dahin fehlen Raumtemperaturen
        und Wärme heute, und der Export pausiert.
      </p>
    {/if}

    <!-- Full width: the room list can grow long and must not stretch a row of other cards. -->
    <section class="card" aria-labelledby="rooms-title">
      <h3 id="rooms-title">Räume</h3>
      <p class="secondary">
        Wählen Sie Temperatursensoren Ihrer Räume und ordnen Sie sie einer Heizzone zu. Das Rundinstrument jeder Zone
        zeigt den Mittelwert ihrer Räume. Werte, die fehlen oder älter als 30 Minuten sind, zählen nicht mit.
      </p>
      {#if rooms.length > 0}
        <ul class="rooms">
          {#each rooms as room (room.entity_id)}
            <li class="room">
              <div class="fields">
                <label>
                  <span>Name</span>
                  <input type="text" maxlength="60" bind:value={room.name} oninput={() => (justSaved = false)} />
                </label>
                <label>
                  <span>Zone</span>
                  <select bind:value={room.zone_id} onchange={() => (justSaved = false)}>
                    <option value={null}>keine Zone</option>
                    {#each zoneOptions as zone (zone.id)}
                      <option value={zone.id}>{zone.label}</option>
                    {/each}
                  </select>
                </label>
              </div>
              <div class="row-end">
                <span class="entity">{room.entity_id}</span>
                <button
                  type="button"
                  class="button small"
                  aria-label={`Raum ${room.name || room.entity_id} entfernen`}
                  onclick={() => removeRoom(room.entity_id)}>Entfernen</button
                >
              </div>
            </li>
          {/each}
        </ul>
      {:else}
        <p class="hint">Noch keine Räume gewählt.</p>
      {/if}
      {#if available}
        <div class="picker-width">
          <EntityPicker
            kind="temperature"
            label="Raum hinzufügen"
            exclude={rooms.map((room) => room.entity_id)}
            onPick={addRoom}
          />
        </div>
      {/if}
    </section>

    <div class="pair">
      <section class="card" aria-labelledby="heat-title">
        <h3 id="heat-title">Wärme heute</h3>
        <p class="secondary">
          Sensoren mit der heute erzeugten Wärme, die jeden Tag bei 0 beginnen – etwa „Heizung“ und „Warmwasser“ der
          myVAILLANT-Integration oder ein Verbrauchszähler-Helfer mit täglichem Zyklus. Die App addiert sie; zusammen
          mit „Energie heute“ ergibt sich die Arbeitszahl des Tages.
        </p>
        {#if heatEntities.length > 0}
          <ul class="heat">
            {#each heatEntities as entityId (entityId)}
              <li class="row-end">
                <span>
                  {#if heatNames[entityId]}
                    {heatNames[entityId]} <span class="entity">({entityId})</span>
                  {:else}
                    <span class="entity">{entityId}</span>
                  {/if}
                </span>
                <button
                  type="button"
                  class="button small"
                  aria-label={`Wärmesensor ${heatNames[entityId] ?? entityId} entfernen`}
                  onclick={() => removeHeat(entityId)}>Entfernen</button
                >
              </li>
            {/each}
          </ul>
        {:else}
          <p class="hint">Kein Sensor gewählt.</p>
        {/if}
        {#if available && heatEntities.length < HEAT_SENSORS_MAX}
          <EntityPicker kind="energy" label="Sensor hinzufügen" exclude={heatEntities} onPick={addHeat} />
        {/if}
      </section>

      <section class="card" aria-labelledby="export-title">
        <h3 id="export-title">Export nach Home Assistant</h3>
        <p class="secondary">
          Die App meldet diese Werte als Sensoren <code>sensor.vaillant_eebus_…</code> an Home Assistant, etwa für
          Automationen und das Energie-Dashboard. Nach einem Neustart von Home Assistant meldet sie sie neu.
        </p>
        <fieldset>
          <legend class="sr-only">Gruppen</legend>
          {#each EXPORT_GROUPS as item (item.group)}
            <label class="check">
              <input
                type="checkbox"
                checked={exportGroups.includes(item.group)}
                onchange={(event) => toggleGroup(item.group, event.currentTarget.checked)}
              />
              <span>
                <span class="check-label">{item.label}</span>
                <span class="hint">{item.hint}</span>
              </span>
            </label>
          {/each}
        </fieldset>
        {#if saved.exported.length > 0}
          <details>
            <summary>Gemeldete Sensoren ({saved.exported.length})</summary>
            <ul class="exported">
              {#each saved.exported as entityId (entityId)}
                <li><code>{entityId}</code></li>
              {/each}
            </ul>
          </details>
        {/if}
      </section>
    </div>

    <div class="actions">
      <button type="button" class="button primary" disabled={!changed || !roomsValid || saving} onclick={save}>
        Speichern
      </button>
      {#if saveError}
        <p class="error" role="alert">{saveError}</p>
      {:else if !roomsValid}
        <p class="hint">Jeder Raum braucht einen Namen.</p>
      {:else if justSaved && !changed}
        <p class="hint" role="status">Gespeichert.</p>
      {/if}
    </div>
  {/if}
</div>

<style>
  .home-assistant {
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

  .rooms {
    margin: 0;
    padding: 0;
    list-style: none;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 16px;
  }

  .room {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px;
    border: 1px solid var(--divider);
    border-radius: 12px;
  }

  .fields {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 12px;
  }

  .fields label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 13px;
    color: var(--text-secondary);
  }

  .picker-width {
    max-width: 560px;
  }

  .pair {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    gap: 24px;
    align-items: start;
  }

  .heat {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .row-end {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }

  .entity {
    font-size: 12px;
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
    gap: 10px;
  }

  .check {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    min-height: 44px;
    cursor: pointer;
  }

  .check input {
    width: 20px;
    height: 20px;
    margin-top: 2px;
    flex-shrink: 0;
  }

  .check > span {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .check-label {
    font-weight: 500;
  }

  details summary {
    cursor: pointer;
    font-size: 14px;
    color: var(--text-secondary);
  }

  .exported {
    margin: 8px 0 0;
    padding-left: 18px;
    font-size: 13px;
  }

  code {
    font-size: 13px;
    word-break: break-all;
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
    .fields,
    .pair,
    .rooms {
      grid-template-columns: 1fr;
    }
  }
</style>
