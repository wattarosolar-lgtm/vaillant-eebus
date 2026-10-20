<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiError, downloadLog, getLog } from '../lib/api';
  import { LOG_CATEGORY_LABELS } from '../lib/texts';
  import type { LogCategory, LogEntry, LogFilter } from '../lib/types';

  type FilterKey = 'all' | 'problems' | LogCategory;

  const CATEGORIES: LogCategory[] = ['write', 'device', 'connection', 'mode', 'settings'];
  const FILTERS: { key: FilterKey; label: string }[] = [
    { key: 'all', label: 'Alle' },
    { key: 'problems', label: 'Probleme' },
    ...CATEGORIES.map((category) => ({ key: category, label: LOG_CATEGORY_LABELS[category] })),
  ];
  // New entries are looked for this often while the newest ones are shown.
  const REFRESH_MS = 10_000;

  let filterKey = $state<FilterKey>('all');
  // Last local day shown (YYYY-MM-DD); null shows the newest entries.
  let until = $state<string | null>(null);
  let dateInput = $state('');
  let entries = $state<LogEntry[]>([]);
  let more = $state(false);
  let loading = $state(false);
  let downloading = $state(false);
  let error = $state<string | null>(null);
  // Answers for an older filter or date are dropped.
  let generation = 0;

  const filter = $derived<LogFilter>({
    category: filterKey === 'all' || filterKey === 'problems' ? null : filterKey,
    problems: filterKey === 'problems',
  });

  const timeFormat = new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const dayFormat = new Intl.DateTimeFormat('de-DE', {
    weekday: 'long',
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });

  function dayKey(date: Date): string {
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${date.getFullYear()}-${month}-${day}`;
  }

  function dayLabel(date: Date): string {
    const today = new Date();
    const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    const key = dayKey(date);
    if (key === dayKey(today)) {
      return 'Heute';
    }
    if (key === dayKey(yesterday)) {
      return 'Gestern';
    }
    return dayFormat.format(date);
  }

  /** Entries grouped by local day, newest day first. */
  const days = $derived.by(() => {
    const groups: { key: string; label: string; entries: LogEntry[] }[] = [];
    for (const entry of entries) {
      const date = new Date(entry.time);
      const key = dayKey(date);
      const last = groups.at(-1);
      if (last?.key === key) {
        last.entries.push(entry);
      } else {
        groups.push({ key, label: dayLabel(date), entries: [entry] });
      }
    }
    return groups;
  });

  /** Newest entries again (older = false) or the next older page. */
  async function load(older: boolean): Promise<void> {
    const current = ++generation;
    loading = true;
    error = null;
    try {
      const page = await getLog(filter, {
        before: older ? entries.at(-1)?.id : undefined,
        until: until ?? undefined,
      });
      if (current !== generation) {
        return;
      }
      entries = older ? [...entries, ...page.entries] : page.entries;
      more = page.more;
    } catch (err) {
      if (current === generation) {
        error = err instanceof ApiError ? err.message : 'Das Protokoll konnte nicht geladen werden.';
      }
    } finally {
      if (current === generation) {
        loading = false;
      }
    }
  }

  /** Adds entries recorded since the newest one shown. */
  async function refresh(): Promise<void> {
    if (until !== null || loading || document.visibilityState !== 'visible') {
      return;
    }
    const current = generation;
    try {
      const page = await getLog(filter, { after: entries[0]?.id ?? 0 });
      if (current !== generation || page.entries.length === 0) {
        return;
      }
      if (page.more) {
        await load(false);
      } else {
        entries = [...page.entries, ...entries];
      }
    } catch {
      // The next round tries again; a lasting problem shows on the next load.
    }
  }

  function choose(key: FilterKey): void {
    filterKey = key;
    void load(false);
  }

  function jump(): void {
    until = dateInput || null;
    void load(false);
  }

  function newest(): void {
    dateInput = '';
    until = null;
    void load(false);
  }

  async function saveFile(): Promise<void> {
    downloading = true;
    error = null;
    try {
      await downloadLog(filter);
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Das Protokoll konnte nicht heruntergeladen werden.';
    } finally {
      downloading = false;
    }
  }

  onMount(() => {
    void load(false);
    const timer = setInterval(() => void refresh(), REFRESH_MS);
    return () => clearInterval(timer);
  });
</script>

<section class="card" aria-labelledby="log-title">
  <div class="head">
    <h2 id="log-title">Protokoll</h2>
    <button type="button" class="button" disabled={downloading} onclick={() => void saveFile()}>
      Herunterladen (CSV)
    </button>
  </div>
  <p class="secondary">
    Alle Schreibvorgänge mit Ergebnis, Änderungen am Gerät, Verbindungsereignisse und Moduswechsel. Neueste zuerst;
    neue Einträge erscheinen von selbst.
  </p>

  <div class="tools">
    <div class="segments filters" role="group" aria-label="Einträge filtern">
      {#each FILTERS as option (option.key)}
        <button
          type="button"
          class:active={option.key === filterKey}
          aria-pressed={option.key === filterKey}
          onclick={() => choose(option.key)}>{option.label}</button
        >
      {/each}
    </div>
    <div class="jump">
      <label for="log-until">Springen zu</label>
      <input id="log-until" type="date" max={dayKey(new Date())} bind:value={dateInput} onchange={jump} />
      {#if until !== null}
        <button type="button" class="button" onclick={newest}>Neueste</button>
      {/if}
    </div>
  </div>

  {#if error}
    <p class="banner error" role="alert">{error}</p>
  {/if}

  {#if days.length > 0}
    {#each days as day (day.key)}
      <section class="day" aria-label={day.label}>
        <h3>{day.label}</h3>
        <ol class="entries">
          {#each day.entries as entry (entry.id)}
            <li class:problem={entry.problem}>
              <time datetime={entry.time}>{timeFormat.format(new Date(entry.time))}</time>
              <span class="category {entry.category}">{LOG_CATEGORY_LABELS[entry.category]}</span>
              <span class="message">
                {#if entry.problem}<span class="tag">Problem</span>{/if}
                {entry.message}
              </span>
            </li>
          {/each}
        </ol>
      </section>
    {/each}
  {:else if !loading && !error}
    <p class="hint">{until !== null ? 'Keine Einträge bis zu diesem Tag.' : 'Keine Einträge.'}</p>
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
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .tools {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .filters {
    flex-wrap: wrap;
  }

  .filters button {
    padding: 0 14px;
  }

  .filters button:not(.active) {
    color: var(--text-secondary);
  }

  .jump {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    color: var(--text-secondary);
  }

  .day {
    display: flex;
    flex-direction: column;
  }

  .day h3 {
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
    font-size: 15px;
    font-weight: 600;
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
    grid-template-columns: 76px 110px 1fr;
    align-items: baseline;
    gap: 12px;
    padding: 10px 0;
    border-bottom: 1px solid var(--divider);
    font-size: 14px;
  }

  .entries li:last-child {
    border-bottom: 0;
  }

  .entries li.problem {
    box-shadow: inset 3px 0 0 var(--error-text);
    padding-left: 10px;
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

  .tag {
    margin-right: 6px;
    padding: 1px 8px;
    border-radius: 999px;
    background: var(--error-bg);
    color: var(--error-text);
    font-size: 12px;
    font-weight: 600;
  }

  @media (max-width: 700px) {
    .entries li {
      grid-template-columns: auto 1fr;
    }

    .message {
      grid-column: 1 / -1;
    }
  }
</style>
