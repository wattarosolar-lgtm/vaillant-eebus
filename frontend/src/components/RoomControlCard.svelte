<script lang="ts">
  import { allRoomsSweep, ApiError, getAutomation, saveProbe, setMqttBaseTopic, setRoomControl } from '../lib/api';
  import { duration, maybe, moment } from '../lib/format';
  import { ROOM_CONTROL_HINTS, ROOM_CONTROL_LABELS } from '../lib/texts';
  import type { RoomControlMode, RoomControlStatus, RoomsPage } from '../lib/types';
  import EntityPicker from './EntityPicker.svelte';
  import StatusPill from './StatusPill.svelte';

  let {
    control,
    rooms,
    homeAssistantAvailable,
    onChange,
  }: {
    control: RoomControlStatus;
    /** Rooms with a thermostat, for "the pair hangs at". */
    rooms: { id: string; name: string }[];
    homeAssistantAvailable: boolean;
    onChange: (page: RoomsPage) => void;
  } = $props();

  const MODES: RoomControlMode[] = ['off', 'observe', 'preview', 'control'];

  let switching = $state(false);
  let error = $state<string | null>(null);
  let yaml = $state<string | null>(null);
  let copied = $state(false);
  let probeFlow = $state<string | null>(null);
  let probeReturn = $state<string | null>(null);
  let probeRoom = $state<string>('');
  let editingProbe = $state(false);
  let probeError = $state<string | null>(null);
  let baseTopic = $state<string | null>(null);
  let topicError = $state<string | null>(null);

  async function storeBaseTopic(): Promise<void> {
    if (baseTopic === null) {
      return;
    }
    topicError = null;
    try {
      onChange(await setMqttBaseTopic(baseTopic.trim()));
      baseTopic = null;
    } catch (err) {
      topicError = err instanceof ApiError ? err.message : 'Das Basis-Topic konnte nicht gespeichert werden.';
    }
  }

  const live = $derived(control.app_mode === 'live');
  const sweeping = $derived(control.sweep_queue.length > 0 || control.rooms.some((room) => room.sweep?.state === 'running' && !room.sweep.part_of_test));
  let sweepBusy = $state(false);
  let sweepError = $state<string | null>(null);

  async function sweepAll(action: 'start' | 'cancel'): Promise<void> {
    let confirm = false;
    if (action === 'start') {
      confirm = window.confirm(
        'Schneller Durchlauf für alle Räume nacheinander: Jedes Thermostat mit Ventilstellung bekommt etwa 30 Minuten ' +
          'lang alle 2 Minuten einen neuen Sollwert (höchstens 20 Befehle je Raum, nicht im Tageskontingent) und ' +
          'danach wieder seinen alten. Räume mit offenem Fenster oder zu warm werden übersprungen. Starten?',
      );
      if (!confirm) {
        return;
      }
    }
    sweepBusy = true;
    sweepError = null;
    try {
      onChange(await allRoomsSweep(action, confirm));
    } catch (err) {
      sweepError = err instanceof ApiError ? err.message : 'Das hat nicht geklappt.';
    } finally {
      sweepBusy = false;
    }
  }

  async function switchMode(mode: RoomControlMode): Promise<void> {
    let confirm = false;
    if (mode === 'control') {
      confirm = window.confirm(
        'Raumregelung auf „Steuern“: Räume, die selbst auf „Steuern“ stehen, bekommen ab jetzt ihre Sollwerte von der ' +
          'App. Bitte vorher die Notfall-Automation in Home Assistant anlegen. Fortfahren?',
      );
      if (!confirm) {
        return;
      }
    }
    switching = true;
    error = null;
    try {
      onChange(await setRoomControl(mode, confirm));
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Die Stufe konnte nicht geändert werden.';
    } finally {
      switching = false;
    }
  }

  function editProbe(): void {
    probeFlow = control.probe.flow_entity;
    probeReturn = control.probe.return_entity;
    probeRoom = control.probe.room_id ?? '';
    probeError = null;
    editingProbe = true;
  }

  async function storeProbe(): Promise<void> {
    probeError = null;
    try {
      onChange(await saveProbe(probeFlow, probeReturn, probeRoom === '' ? null : probeRoom));
      editingProbe = false;
    } catch (err) {
      probeError = err instanceof ApiError ? err.message : 'Das Fühlerpaar konnte nicht gespeichert werden.';
    }
  }

  async function swapProbe(): Promise<void> {
    probeError = null;
    try {
      onChange(await saveProbe(control.probe.return_entity, control.probe.flow_entity, control.probe.room_id));
    } catch (err) {
      probeError = err instanceof ApiError ? err.message : 'Das Fühlerpaar konnte nicht getauscht werden.';
    }
  }

  /** "26,4 °C · vor 12 min" for a sensor of the pair. */
  function reading(celsius: number | null, ageMin: number | null): string {
    const value = maybe(celsius, 1, '°C');
    return ageMin === null ? value : `${value} · gemeldet vor ${duration(ageMin)}`;
  }

  async function showAutomation(): Promise<void> {
    try {
      yaml = await getAutomation();
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

<section class="card control" aria-labelledby="room-control-title">
  <div class="head">
    <h3 id="room-control-title">Raumregelung</h3>
    <StatusPill label={ROOM_CONTROL_LABELS[control.mode]} tone={control.mode === 'control' ? 'ok' : 'neutral'} />
  </div>
  <p class="secondary">
    Die App gibt jedem Heizkörperthermostat seinen Sollwert vor, damit der Raum sein Soll hält. Jeder Raum hat dazu
    eine eigene Stufe; es gilt die kleinere von beiden. „Steuern“ geht nur im Live-Betrieb und erst, wenn die App vom
    Thermostat „zu“ und „ganz offen“ kennt – aus einem schnellen Durchlauf, einem Testlauf oder dem Alltag.
  </p>
  <div class="segments stage" role="group" aria-label="Stufe der Raumregelung">
    {#each MODES as mode (mode)}
      {@const blocked = mode === 'control' && !live}
      <button
        type="button"
        class:active={control.mode === mode}
        aria-pressed={control.mode === mode}
        disabled={switching || blocked}
        title={blocked ? '„Steuern“ geht nur im Live-Betrieb.' : undefined}
        onclick={() => void switchMode(mode)}>{ROOM_CONTROL_LABELS[mode]}</button
      >
    {/each}
  </div>
  <p class="hint">{ROOM_CONTROL_HINTS[control.mode]}{live ? '' : ' Im Demo-Betrieb sendet die App nichts.'}</p>
  {#if error}
    <p class="error" role="alert">{error}</p>
  {/if}
  {#if control.problems.length > 0}
    <ul class="problems">
      {#each control.problems as problem (problem)}
        <li>{problem}</li>
      {/each}
    </ul>
  {/if}

  <p class="hint">
    Lebenszeichen an Home Assistant (<code>{control.lifesign_entity}</code>):
    {control.lifesign_at ? `zuletzt ${moment(control.lifesign_at)} Uhr` : 'noch keins'}
  </p>

  <details>
    <summary>Ventilstellung der Aqara W600 abfragen</summary>
    <div class="details-body">
      <p class="hint">
        Der W600 meldet seine Ventilstellung nur selten. Die App fragt sie deshalb selbst ab – alle 5 Minuten, kurz nach
        jedem neuen Sollwert und im Testlauf jede Minute – mit einer Nachricht an Zigbee2MQTT
        (<code>{control.mqtt_base_topic}/&lt;Gerät&gt;/get</code> mit <code>{'{"running_state": ""}'}</code>). Das
        liest nur und verstellt nichts, auch im Demo-Betrieb. Dafür braucht Home Assistant die MQTT-Integration.
      </p>
      <p class="hint">
        Tipp: In Zigbee2MQTT „Zuletzt gesehen“ einschalten (Einstellungen → Erweitert → last_seen = ISO_8601) und in Home
        Assistant die Entitäten „… Last seen“ der Thermostate aktivieren. Dann sieht die App, ob ein Thermostat auf die
        Abfrage antwortet, und meldet ein stilles Thermostat erst, wenn es wirklich schweigt.
      </p>
      <label class="field topic">
        <span class="field-label">Basis-Topic von Zigbee2MQTT</span>
        <input
          type="text"
          maxlength="100"
          autocomplete="off"
          value={baseTopic ?? control.mqtt_base_topic}
          oninput={(event) => (baseTopic = event.currentTarget.value)}
        />
      </label>
      {#if baseTopic !== null && baseTopic !== control.mqtt_base_topic}
        <div class="row">
          <button type="button" class="button small" onclick={() => void storeBaseTopic()}>Speichern</button>
          <button type="button" class="button small" onclick={() => (baseTopic = null)}>Verwerfen</button>
        </div>
      {/if}
      {#if topicError}
        <p class="error" role="alert">{topicError}</p>
      {/if}
    </div>
  </details>

  {#if control.mode !== 'off' && live}
    <div class="sweep-all">
      <span class="field-label">Schneller Durchlauf</span>
      {#if sweeping}
        <p class="hint">
          Läuft{control.sweep_queue.length > 0 ? `; danach noch: ${control.sweep_queue.join(', ')}` : ''}.
        </p>
        <div>
          <button type="button" class="button small" disabled={sweepBusy} onclick={() => void sweepAll('cancel')}
            >Durchläufe abbrechen</button
          >
        </div>
      {:else}
        <p class="hint">
          Lernt in etwa 25 Minuten je Raum, ab wann jedes Ventil öffnet und schließt – danach ist „Steuern“ möglich.
        </p>
        <div>
          <button type="button" class="button small" disabled={sweepBusy} onclick={() => void sweepAll('start')}
            >Alle Räume nacheinander</button
          >
        </div>
      {/if}
      {#if sweepError}
        <p class="error" role="alert">{sweepError}</p>
      {/if}
    </div>
  {/if}

  <details>
    <summary>Notfall-Automation für Home Assistant</summary>
    <div class="details-body">
      <p class="hint">
        Fällt die App aus, bleiben die letzten Sollwerte an den Thermostaten stehen. Diese Automation setzt nach 15
        Minuten ohne Lebenszeichen jedes Thermostat auf seinen Notfallwert (Raumdaten → Notfallwert). Anlegen in Home
        Assistant: Einstellungen → Automationen &amp; Szenen → Automation erstellen → ⋮ → „In YAML bearbeiten“, Text
        einfügen, speichern. Prüfen: die App stoppen; nach 15 Minuten stehen die Notfallwerte an den Thermostaten.
      </p>
      {#if yaml === null}
        <div><button type="button" class="button small" onclick={() => void showAutomation()}>Vorlage anzeigen</button></div>
      {:else}
        <pre aria-label="Vorlage der Notfall-Automation">{yaml}</pre>
        <div class="row">
          <button type="button" class="button small" onclick={() => void copy()}>Kopieren</button>
          <span class="hint" role="status">{copied ? 'Kopiert.' : ''}</span>
        </div>
      {/if}
    </div>
  </details>

  <details>
    <summary>Fühlerpaar für die Testläufe{control.probe.swapped ? ' – vertauscht?' : ''}</summary>
    <div class="details-body">
      <p class="hint">
        Ein Testlauf misst nachts ({control.test_start}–{control.test_end} Uhr, Start spätestens
        {control.test_latest_start} Uhr), wie das Thermostat auf seinen Sollwert reagiert. Dafür hängt ein Paar
        Anlegefühler an Vor- und Rücklauf des Heizkörpers (gut anliegend, mit Isolierung darüber). Der Vorlauf ist das
        Rohr am Thermostatventil. Fühler, die nur bei einer Änderung melden, sind in Ordnung: Ein Wert gilt bis
        {maybe(control.probe.valid_hours, 0, 'h')} nach seiner Meldung. Gut ist eine Auflösung von 0,1–0,2 K.
      </p>
      {#if control.probe.swapped}
        <p class="warn" role="alert">
          Der Vorlauf ist kälter als der Rücklauf, obwohl warmes Wasser am offenen Ventil ankommt: Sind die Fühler
          vertauscht oder liegt der Vorlauf-Fühler schlecht an? Solange startet kein Testlauf.
        </p>
        {#if control.probe.flow_entity && control.probe.return_entity}
          <div>
            <button type="button" class="button small" onclick={() => void swapProbe()}>Vorlauf und Rücklauf tauschen</button>
          </div>
        {/if}
      {/if}
      {#if probeError && !editingProbe}
        <p class="error" role="alert">{probeError}</p>
      {/if}
      <dl class="grid-facts">
        <div>
          <dt>Vorlauf</dt>
          <dd>
            {reading(control.probe.flow_c, control.probe.flow_age_min)}
            <span class="hint">{control.probe.flow_entity ?? 'nicht gewählt'}</span>
          </dd>
        </div>
        <div>
          <dt>Rücklauf</dt>
          <dd>
            {reading(control.probe.return_c, control.probe.return_age_min)}
            <span class="hint">{control.probe.return_entity ?? 'nicht gewählt'}</span>
          </dd>
        </div>
        <div>
          <dt>Hängt an</dt>
          <dd>
            {control.probe.room_name ?? 'keinem Heizkörper'}
            {#if control.probe.since}<span class="hint">seit {moment(control.probe.since)} Uhr</span>{/if}
          </dd>
        </div>
        <div>
          <dt>Längste Meldepause / kleinste Änderung</dt>
          <dd>{duration(control.probe.longest_gap_min)} / {maybe(control.probe.resolution_k, 2, 'K')}</dd>
        </div>
      </dl>
      {#if editingProbe}
        <form
          class="probe"
          aria-label="Fühlerpaar"
          onsubmit={(event) => {
            event.preventDefault();
            void storeProbe();
          }}
        >
          {#each [{ label: 'Vorlauf-Fühler', value: probeFlow, set: (value: string | null) => (probeFlow = value) }, { label: 'Rücklauf-Fühler', value: probeReturn, set: (value: string | null) => (probeReturn = value) }] as item (item.label)}
            <div class="field">
              <span class="field-label">{item.label}</span>
              {#if item.value}
                <div class="row">
                  <code>{item.value}</code>
                  <button type="button" class="button small" onclick={() => item.set(null)}>Entfernen</button>
                </div>
              {:else if homeAssistantAvailable}
                <EntityPicker kind="temperature" label={`${item.label} wählen`} onPick={(choice) => item.set(choice.entity_id)} />
              {/if}
            </div>
          {/each}
          <label class="field">
            <span class="field-label">Hängt am Heizkörper von</span>
            <select bind:value={probeRoom}>
              <option value="">keinem (woanders)</option>
              {#each rooms as room (room.id)}
                <option value={room.id}>{room.name}</option>
              {/each}
            </select>
          </label>
          <div class="row">
            <button type="submit" class="button primary small">Speichern</button>
            <button type="button" class="button small" onclick={() => (editingProbe = false)}>Abbrechen</button>
          </div>
          {#if probeError}
            <p class="error" role="alert">{probeError}</p>
          {/if}
        </form>
      {:else}
        <div><button type="button" class="button small" onclick={editProbe}>Fühlerpaar ändern</button></div>
      {/if}
    </div>
  </details>
</section>

<style>
  .control {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
  }

  .head h3 {
    font-size: 18px;
    font-weight: 600;
  }

  .segments.stage {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  .segments.stage > button {
    padding: 0 4px;
  }

  details summary {
    cursor: pointer;
    min-height: 44px;
    display: flex;
    align-items: center;
    font-size: 15px;
    font-weight: 500;
  }

  .details-body {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding-bottom: 8px;
  }

  pre {
    margin: 0;
    padding: 12px;
    max-height: 320px;
    overflow: auto;
    background: var(--surface-muted);
    border-radius: var(--radius-control);
    font-size: 12px;
    white-space: pre;
  }

  dl.grid-facts {
    margin: 0;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px 20px;
  }

  dl.grid-facts > div {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  dl.grid-facts dt {
    font-size: 13px;
    color: var(--text-secondary);
  }

  dl.grid-facts dd {
    margin: 0;
    text-align: left;
    font-size: 15px;
    overflow-wrap: anywhere;
  }

  .probe {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }

  .sweep-all {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .sweep-all p {
    margin: 0;
  }

  .field-label {
    font-size: 13px;
    color: var(--text-secondary);
  }

  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }

  code {
    font-size: 13px;
    color: var(--text-hint);
    word-break: break-all;
  }

  .problems {
    margin: 0;
    padding-left: 20px;
    color: var(--warn-text);
    font-size: 14px;
  }

  .warn {
    color: var(--warn-text);
    font-size: 14px;
  }

  .error {
    color: var(--error-text);
    font-size: 14px;
  }

  .button.small {
    min-height: 44px;
    padding: 0 14px;
    font-size: 14px;
  }

  @media (max-width: 420px) {
    dl.grid-facts {
      grid-template-columns: 1fr;
    }
  }
</style>
