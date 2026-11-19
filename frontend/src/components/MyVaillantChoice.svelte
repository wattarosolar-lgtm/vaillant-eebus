<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiError, discoverMyVaillant, getEntities } from '../lib/api';
  import type { EntityKind, MyVaillantEntities } from '../lib/types';
  import EntityPicker from './EntityPicker.svelte';

  let {
    initial,
    available,
    busy = false,
    submitLabel,
    onSubmit,
  }: {
    /** The saved choice; an empty one is filled with what the app finds in Home Assistant. */
    initial: MyVaillantEntities;
    /** False outside Home Assistant: nothing can be chosen. */
    available: boolean;
    busy?: boolean;
    submitLabel: string;
    onSubmit: (entities: MyVaillantEntities) => void;
  } = $props();

  // A copy of the saved choice, edited here until the user submits it.
  // svelte-ignore state_referenced_locally
  let draft = $state<MyVaillantEntities>({ ...initial, zones: [...initial.zones] });
  let names = $state<Record<string, string>>({});
  let searching = $state(false);
  let error = $state<string | null>(null);

  const configured = $derived(draft.zones.length > 0 || draft.hot_water !== null);

  async function loadNames(): Promise<void> {
    for (const kind of ['climate', 'water_heater', 'temperature', 'power'] as EntityKind[]) {
      for (const choice of await getEntities(kind, '')) {
        names[choice.entity_id] = choice.name;
      }
    }
  }

  async function discover(): Promise<void> {
    searching = true;
    error = null;
    try {
      const found = await discoverMyVaillant();
      draft = found;
      if (found.zones.length === 0 && found.hot_water === null) {
        error =
          'In Home Assistant wurden keine Entitäten der myVAILLANT-Integration gefunden. Ist die Integration eingerichtet? Sie können die Entitäten auch von Hand wählen.';
      }
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Die Suche ist fehlgeschlagen.';
    } finally {
      searching = false;
    }
  }

  onMount(() => {
    if (!available) {
      return;
    }
    void loadNames().catch(() => undefined);
    if (initial.zones.length === 0 && initial.hot_water === null) {
      void discover();
    }
  });
</script>

{#snippet single(
  label: string,
  kind: EntityKind,
  value: string | null,
  set: (value: string | null) => void,
)}
  <div class="entity-field">
    <span class="field-label">{label}</span>
    {#if value}
      <div class="row-end">
        <span>
          {#if names[value]}{names[value]}{/if}
          <code>{value}</code>
        </span>
        <button type="button" class="button small" aria-label={`${label} entfernen`} onclick={() => set(null)}
          >Entfernen</button
        >
      </div>
    {:else}
      <EntityPicker
        {kind}
        label={`${label} wählen`}
        onPick={(choice) => {
          names[choice.entity_id] = choice.name;
          set(choice.entity_id);
        }}
      />
    {/if}
  </div>
{/snippet}

{#if !available}
  <p class="banner info" role="status">
    myVAILLANT geht nur, wenn die App in Home Assistant läuft: Die Werte kommen aus der myVAILLANT-Integration.
  </p>
{:else}
  <div class="choice">
    <div class="row-end">
      <p class="secondary">
        Die App schlägt die Entitäten der myVAILLANT-Integration vor. Prüfen Sie die Auswahl und passen Sie sie bei Bedarf an.
      </p>
      <button type="button" class="button small" onclick={() => void discover()} disabled={searching}>
        {searching ? 'Sucht …' : 'Neu suchen'}
      </button>
    </div>
    {#if error}
      <p class="banner warn" role="status">{error}</p>
    {/if}

    <div class="entity-field">
      <span class="field-label">Heizzonen (Klima-Entitäten)</span>
      {#if draft.zones.length > 0}
        <ul class="zones">
          {#each draft.zones as zone (zone)}
            <li class="row-end">
              <span>
                {#if names[zone]}{names[zone]}{/if}
                <code>{zone}</code>
              </span>
              <button
                type="button"
                class="button small"
                aria-label={`Zone ${zone} entfernen`}
                onclick={() => (draft.zones = draft.zones.filter((entry) => entry !== zone))}>Entfernen</button
              >
            </li>
          {/each}
        </ul>
      {:else}
        <p class="hint">Keine Zone gewählt.</p>
      {/if}
      <EntityPicker
        kind="climate"
        label="Zone hinzufügen"
        exclude={draft.zones}
        onPick={(choice) => {
          names[choice.entity_id] = choice.name;
          draft.zones = [...draft.zones, choice.entity_id];
        }}
      />
    </div>

    {@render single('Warmwasser', 'water_heater', draft.hot_water, (value) => (draft.hot_water = value))}
    {@render single('Außentemperatur', 'temperature', draft.outdoor_temperature, (value) => (draft.outdoor_temperature = value))}
    {@render single('Leistung der Wärmepumpe (optional)', 'power', draft.power, (value) => (draft.power = value))}
    <p class="hint">
      Ohne Leistungssensor gibt es keine „Energie heute“, und der Planer rechnet die Wärmepumpe in den Hausverbrauch ein.
      Die myVAILLANT-Integration liefert die Leistung nur bei manchen Anlagen („Current Power“).
    </p>

    {#if !configured}
      <p class="hint">Bitte mindestens eine Zone oder das Warmwasser wählen.</p>
    {/if}
    <div>
      <button
        type="button"
        class="button primary"
        disabled={!configured || busy}
        onclick={() => onSubmit({ ...draft, zones: [...draft.zones] })}
      >
        {busy ? 'Wird gespeichert …' : submitLabel}
      </button>
    </div>
  </div>
{/if}

<style>
  .choice {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .entity-field {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .field-label {
    font-size: 13px;
    color: var(--text-secondary);
  }

  .row-end {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px 12px;
  }

  .zones {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  code {
    font-family: inherit;
    font-size: 13px;
    color: var(--text-secondary);
    margin-left: 6px;
    overflow-wrap: anywhere;
  }
</style>
