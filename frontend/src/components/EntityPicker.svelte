<script lang="ts">
  import { onDestroy } from 'svelte';

  import { ApiError, getEntities } from '../lib/api';
  import type { EntityChoice } from '../lib/types';

  let {
    kind,
    label,
    exclude = [],
    onPick,
  }: {
    kind: 'temperature' | 'energy';
    label: string;
    /** Entity IDs already chosen; they are not offered again. */
    exclude?: string[];
    onPick: (choice: EntityChoice) => void;
  } = $props();

  /** Search after the user paused typing. */
  const SEARCH_AFTER_MS = 300;

  const id = `picker-${Math.random().toString(36).slice(2)}`;
  let query = $state('');
  let choices = $state<EntityChoice[] | null>(null);
  let error = $state<string | null>(null);
  let loading = $state(false);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let request = 0;

  const shown = $derived((choices ?? []).filter((choice) => !exclude.includes(choice.entity_id)));

  async function search(): Promise<void> {
    const mine = ++request;
    loading = true;
    try {
      const found = await getEntities(kind, query);
      if (mine === request) {
        choices = found;
        error = null;
      }
    } catch (err) {
      if (mine === request) {
        error = err instanceof ApiError ? err.message : 'Die Suche ist fehlgeschlagen.';
      }
    } finally {
      if (mine === request) {
        loading = false;
      }
    }
  }

  function searchLater(): void {
    clearTimeout(timer);
    timer = setTimeout(() => void search(), SEARCH_AFTER_MS);
  }

  function pick(choice: EntityChoice): void {
    onPick(choice);
    query = '';
    choices = null;
  }

  onDestroy(() => clearTimeout(timer));
</script>

<div class="picker">
  <label for={id}>{label}</label>
  <input
    {id}
    type="search"
    placeholder="Name oder Entitäts-ID, leer für alle"
    autocomplete="off"
    bind:value={query}
    oninput={searchLater}
    onfocus={() => choices === null && void search()}
  />
  {#if error}
    <p class="error" role="alert">{error}</p>
  {:else if loading && choices === null}
    <p class="hint" role="status">Suche …</p>
  {:else if choices !== null}
    {#if shown.length === 0}
      <p class="hint" role="status">Keine passenden Sensoren gefunden.</p>
    {:else}
      <ul class="choices" aria-label="Gefundene Sensoren">
        {#each shown as choice (choice.entity_id)}
          <li>
            <button type="button" class="choice" onclick={() => pick(choice)}>
              <span class="name">{choice.name}</span>
              <span class="meta">{choice.entity_id} · {choice.state} {choice.unit ?? ''}</span>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</div>

<style>
  .picker {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  label {
    font-size: 13px;
    color: var(--text-secondary);
  }

  input {
    min-height: 44px;
    padding: 0 12px;
    border: 1px solid var(--control-border);
    border-radius: 10px;
    background: var(--surface);
    color: var(--text);
    font: inherit;
  }

  input:focus-visible {
    outline: 3px solid var(--info-text);
    outline-offset: 1px;
  }

  .choices {
    margin: 0;
    padding: 0;
    list-style: none;
    max-height: 260px;
    overflow-y: auto;
    border: 1px solid var(--divider);
    border-radius: 10px;
  }

  .choice {
    width: 100%;
    min-height: 44px;
    padding: 8px 12px;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
    border: 0;
    background: transparent;
    color: var(--text);
    text-align: left;
    cursor: pointer;
  }

  .choice:hover {
    background: var(--bg);
  }

  .choice:focus-visible {
    outline: 3px solid var(--info-text);
    outline-offset: -3px;
  }

  .name {
    font-weight: 500;
  }

  .meta {
    font-size: 12px;
    color: var(--text-hint);
    word-break: break-all;
  }

  .error {
    color: var(--error-text);
    font-size: 13px;
  }
</style>
