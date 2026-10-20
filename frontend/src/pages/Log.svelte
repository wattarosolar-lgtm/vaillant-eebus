<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiError, getLog } from '../lib/api';
  import { LOG_CATEGORY_LABELS } from '../lib/texts';
  import type { LogCategory, LogEntry } from '../lib/types';

  const FILTERS: (LogCategory | null)[] = [null, 'write', 'device', 'connection', 'mode', 'settings'];

  let category = $state<LogCategory | null>(null);
  let entries = $state<LogEntry[]>([]);
  let more = $state(false);
  let loading = $state(false);
  let error = $state<string | null>(null);

  const timeFormat = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });

  /** Newest entries again (older = false) or the next older page. */
  async function load(older: boolean): Promise<void> {
    loading = true;
    error = null;
    try {
      const before = older ? (entries.at(-1)?.id ?? null) : null;
      const page = await getLog(category, before);
      entries = older ? [...entries, ...page.entries] : page.entries;
      more = page.more;
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Das Protokoll konnte nicht geladen werden.';
    } finally {
      loading = false;
    }
  }

  function choose(filter: LogCategory | null): void {
    category = filter;
    void load(false);
  }

  onMount(() => void load(false));
</script>

<section class="card" aria-labelledby="log-title">
  <div class="head">
    <h2 id="log-title">Protokoll</h2>
    <button type="button" class="button" disabled={loading} onclick={() => void load(false)}>Aktualisieren</button>
  </div>
  <p class="secondary">
    Alle Schreibvorgänge mit Ergebnis, Änderungen am Gerät, Verbindungsereignisse und Moduswechsel. Neueste zuerst.
  </p>

  <div class="filters" role="group" aria-label="Einträge filtern">
    {#each FILTERS as filter (filter ?? 'all')}
      <button
        type="button"
        class:active={filter === category}
        aria-pressed={filter === category}
        onclick={() => choose(filter)}>{filter ? LOG_CATEGORY_LABELS[filter] : 'Alle'}</button
      >
    {/each}
  </div>

  {#if error}
    <p class="banner error" role="alert">{error}</p>
  {/if}

  {#if entries.length > 0}
    <ol class="entries">
      {#each entries as entry (entry.id)}
        <li>
          <time datetime={entry.time}>{timeFormat.format(new Date(entry.time))}</time>
          <span class="category {entry.category}">{LOG_CATEGORY_LABELS[entry.category]}</span>
          <span class="message">{entry.message}</span>
        </li>
      {/each}
    </ol>
  {:else if !loading && !error}
    <p class="hint">Keine Einträge.</p>
  {/if}

  {#if loading}
    <p class="hint" role="status">Wird geladen …</p>
  {:else if more}
    <div>
      <button type="button" class="button" onclick={() => void load(true)}>Ältere laden</button>
    </div>
  {/if}
</section>

<style>
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    padding: 4px;
    background: var(--bg);
    border-radius: 12px;
    align-self: flex-start;
  }

  .filters button {
    min-height: 40px;
    padding: 0 14px;
    border: 0;
    border-radius: 9px;
    background: transparent;
    color: var(--text-secondary);
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
  }

  .filters button.active {
    background: var(--text);
    color: #ffffff;
  }

  .filters button:focus-visible {
    outline: 3px solid var(--info-text);
    outline-offset: 2px;
  }

  .entries {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
  }

  .entries li {
    display: grid;
    grid-template-columns: 170px 110px 1fr;
    align-items: baseline;
    gap: 12px;
    padding: 10px 0;
    border-bottom: 1px solid var(--divider);
    font-size: 14px;
  }

  .entries li:last-child {
    border-bottom: 0;
  }

  time {
    color: var(--text-secondary);
  }

  .category {
    justify-self: start;
    padding: 2px 10px;
    border-radius: 999px;
    background: var(--bg);
    color: var(--text-secondary);
    font-size: 13px;
    font-weight: 500;
  }

  .category.write {
    background: var(--live-bg);
    color: var(--live-text);
  }

  .category.device {
    background: var(--info-bg);
    color: var(--info-text);
  }

  .message {
    overflow-wrap: anywhere;
  }

  @media (max-width: 700px) {
    .entries li {
      grid-template-columns: 1fr auto;
    }

    .message {
      grid-column: 1 / -1;
    }
  }
</style>
