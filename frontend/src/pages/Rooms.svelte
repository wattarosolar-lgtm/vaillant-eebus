<script lang="ts">
  import { onMount } from 'svelte';

  import RoomControlCard from '../components/RoomControlCard.svelte';
  import RoomEditor from '../components/RoomEditor.svelte';
  import StatusPill from '../components/StatusPill.svelte';
  import ThermostatPanel from '../components/ThermostatPanel.svelte';
  import { ApiError, getRooms } from '../lib/api';
  import { duration, formatNumber, maybe, moment, signed } from '../lib/format';
  import { live } from '../lib/live.svelte';
  import { FLOW_HINT_LABELS, SUPPLY_LABELS, type Tone } from '../lib/texts';
  import type { RoomControlRoom, RoomsPage, RoomStatus, Supply, ZoneControl } from '../lib/types';
  import { hasUnsaved } from '../lib/unsaved';

  let { homeAssistantAvailable }: { homeAssistantAvailable: boolean } = $props();

  const RELOAD_MS = 60_000;
  // After an event "rooms" the page waits this long for more before it reloads.
  const LIVE_DELAY_MS = 500;

  let view = $state<RoomsPage | null>(null);
  let error = $state<string | null>(null);
  let editing = $state<string | null>(null);

  async function load(): Promise<void> {
    try {
      view = await getRooms();
      error = null;
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Die Räume konnten nicht geladen werden.';
    }
  }

  function loadIfVisible(): void {
    // While a room is edited the list stays as it is.
    if (document.visibilityState === 'visible' && editing === null) {
      void load();
    }
  }

  let liveTimer: number | undefined;

  onMount(() => {
    void load();
    const timer = setInterval(loadIfVisible, RELOAD_MS);
    return () => {
      clearInterval(timer);
      window.clearTimeout(liveTimer);
    };
  });

  // Live: the backend says something on this page changed (thermostat, valve, room sensor, probe, a decision).
  $effect(() => {
    if (live.rooms > 0) {
      window.clearTimeout(liveTimer);
      liveTimer = window.setTimeout(loadIfVisible, LIVE_DELAY_MS);
    }
  });

  const SUPPLY_TONES: Record<Supply, Tone> = { unknown: 'neutral', supplied: 'ok', under: 'warn', over: 'neutral' };
  const LEARNED: { key: string; label: string; show: (room: RoomStatus) => string }[] = [
    { key: 'tau_h', label: 'Zeitkonstante', show: (room) => maybe(room.model?.tau_h, 0, 'h') },
    { key: 'gains_k_per_h', label: 'Fremdwärme', show: (room) => maybe(room.model?.gains_k_per_h, 2, 'K/h') },
    { key: 'heating_rate', label: 'Aufheizen je K Vorlauf', show: (room) => maybe(room.model?.heating_rate, 3, 'K/h') },
    { key: 'startup_min', label: 'Anlaufzeit', show: (room) => duration(room.model?.startup_min) },
    { key: 'demand_valve', label: 'Bedarfsöffnung', show: (room) => maybe(room.model?.demand_valve, 0, '%') },
    { key: 'correction_k', label: 'Korrektur Thermostat', show: (room) => maybe(room.model?.correction_k, 1, 'K') },
    { key: 'overshoot_k', label: 'Überschwingen', show: (room) => maybe(room.model?.overshoot_k, 1, 'K') },
  ];

  function startEdit(entityId: string): void {
    if (editing !== null && editing !== entityId && hasUnsaved() && !window.confirm('Änderungen am anderen Raum verwerfen?')) {
      return;
    }
    editing = entityId;
  }

  function controlOf(entityId: string): RoomControlRoom | undefined {
    return view?.control?.rooms.find((room) => room.room_id === entityId);
  }

  function zoneControl(zoneId: string | undefined): ZoneControl | undefined {
    return view?.control?.zones.find((zone) => zone.zone_id === zoneId);
  }

  const thermostatRooms = $derived(
    (view?.control?.rooms ?? []).filter((room) => room.trv_entity !== null).map((room) => ({ id: room.room_id, name: room.name })),
  );

  function roomsOf(zoneId: string | null): RoomStatus[] {
    return (view?.rooms ?? []).filter((room) => room.zone_id === zoneId);
  }

  const groups = $derived.by(() => {
    if (view === null) {
      return [];
    }
    const result = view.zones.map((zone) => ({ zone, rooms: roomsOf(zone.zone_id) }));
    const known = new Set(view.zones.map((zone) => zone.zone_id));
    const rest = view.rooms.filter((room) => room.zone_id === null || !known.has(room.zone_id));
    return [...result.filter((group) => group.rooms.length > 0), ...(rest.length > 0 ? [{ zone: null, rooms: rest }] : [])];
  });
</script>

<div class="page">
  <div class="section-head">
    <h2>Räume</h2>
  </div>

  {#if error}
    <p class="banner error" role="alert">{error}</p>
  {:else if view === null}
    <p class="secondary" role="status">Räume werden geladen …</p>
  {:else if view.rooms.length === 0}
    <section class="card">
      <p class="secondary">Noch keine Räume. Räume wählen Sie unter Einstellungen → Home Assistant → Räume.</p>
      <div><a class="button small link-button" href="#/einstellungen">Zu den Einstellungen</a></div>
    </section>
  {:else}
    <p class="secondary">
      Die App lernt jeden Raum kennen: wie schnell er abkühlt und aufheizt und wie weit das Ventil dafür offen steht.
      Mit der Raumregelung stellt sie die Heizkörperthermostate: erst nur als Vorschau, dann Raum für Raum.
    </p>
    {#if view.control}
      <RoomControlCard
        control={view.control}
        rooms={thermostatRooms}
        {homeAssistantAvailable}
        onChange={(next) => (view = next)}
      />
    {/if}
    {#each groups as group (group.zone?.zone_id ?? 'none')}
      {@const zoneState = zoneControl(group.zone?.zone_id)}
      <section class="zone" aria-label={group.zone?.label ?? 'Ohne Zone'}>
        <div class="zone-head">
          <h3>{group.zone?.label ?? 'Ohne Zone'}</h3>
          {#if group.zone?.limiting_room}
            <span class="hint">
              Pause begrenzt durch {group.zone.limiting_room}: untere Grenze in {duration(group.zone.limiting_minutes)}
            </span>
          {/if}
          {#if group.zone?.flow}
            <span class="hint">
              Vorlauf (würde): {FLOW_HINT_LABELS[group.zone.flow.hint]}, Verschiebung {signed(group.zone.flow.offset_k)} K
              {#if group.zone.flow.bottleneck}· Engpass {group.zone.flow.bottleneck} ({maybe(group.zone.flow.max_valve, 0, '%')}){/if}
            </span>
          {/if}
          {#if zoneState && view.control && view.control.mode !== 'off'}
            <span class="hint">
              {zoneState.supply_name}{zoneState.remaining_min !== null ? `, noch etwa ${duration(zoneState.remaining_min)}` : ''}
              · Umverteilen: {zoneState.redistribution}{zoneState.cold_room ? ` (für ${zoneState.cold_room})` : ''}
              {#if zoneState.donors.length > 0}· gedrosselt: {zoneState.donors.join(', ')}{/if}
            </span>
            {#if zoneState.flow_hint === 'higher'}
              <p class="banner warn">
                {zoneState.flow_text ?? 'Vorlauf höher nötig'}. Die App hebt den Vorlauf erst mit dem Zonen-Release (1.5.0)
                an; bis dahin geht es von Hand in myVAILLANT.
              </p>
            {:else if zoneState.flow_text}
              <span class="hint">Vorlauf: {zoneState.flow_text}</span>
            {/if}
          {/if}
        </div>
        <div class="rooms">
          {#each group.rooms as room (room.entity_id)}
            {@const thermostat = controlOf(room.entity_id)}
            <article class="card room" aria-labelledby={`room-${room.entity_id}`}>
              <div class="room-head">
                <h4 id={`room-${room.entity_id}`}>{room.name}</h4>
                <div class="pills">
                  {#if room.window_open}
                    <StatusPill label="Fenster offen (würde)" tone="warn" />
                  {/if}
                  {#if room.supply !== 'unknown'}
                    <StatusPill label={SUPPLY_LABELS[room.supply]} tone={SUPPLY_TONES[room.supply]} />
                  {/if}
                  {#if room.model?.learning}
                    <StatusPill label="lernt noch" tone="neutral" />
                  {/if}
                </div>
              </div>
              <dl class="facts">
                <div>
                  <dt>Raumtemperatur{room.measured_at_thermostat ? ' (am Thermostat)' : ''}</dt>
                  <dd>{maybe(room.temperature_c, 1, '°C')}</dd>
                </div>
                <div>
                  <dt>Soll jetzt (Band)</dt>
                  <dd>
                    {formatNumber(room.setpoint_c, 1)} °C
                    <span class="hint">({formatNumber(room.comfort_low_c, 1)} – {formatNumber(room.comfort_high_c, 1)})</span>
                  </dd>
                </div>
                {#if room.details.trv_entity}
                  <div><dt>Thermostat-Soll</dt><dd>{maybe(room.trv_setpoint_c, 1, '°C')}</dd></div>
                {/if}
                {#if room.details.valve_entity}
                  <div><dt>Ventil</dt><dd>{maybe(room.valve, 0, '%')}</dd></div>
                {/if}
                {#if room.details.humidity_entity}
                  <div><dt>Luftfeuchte</dt><dd>{maybe(room.humidity, 0, '%')}</dd></div>
                {/if}
                <div><dt>Abkühlung jetzt</dt><dd>{maybe(room.cooling_now_k_per_h, 2, 'K/h')}</dd></div>
                <div>
                  <dt>Bis zur unteren Grenze ohne Heizen</dt>
                  <dd>{room.minutes_to_lower === null ? 'über 24 h' : duration(room.minutes_to_lower)}</dd>
                </div>
                {#if room.window_since}
                  <div><dt>Fenster offen seit</dt><dd>{moment(room.window_since)} Uhr</dd></div>
                {/if}
              </dl>

              <details>
                <summary>Lernstand</summary>
                <table class="learned">
                  <tbody>
                    {#each LEARNED as item (item.key)}
                      <tr>
                        <th scope="row">{item.label}</th>
                        <td>{item.show(room)}</td>
                        <td class="hint">{room.model?.learned[item.key] ? 'gelernt' : 'Startwert / lernt noch'}</td>
                      </tr>
                    {/each}
                    {#if room.heat_loss_start_w_per_k !== null}
                      <tr>
                        <th scope="row">Wärmeverlust (Gutachten)</th>
                        <td>{maybe(room.heat_loss_start_w_per_k, 0, 'W/K')}</td>
                        <td class="hint">Startwert</td>
                      </tr>
                    {/if}
                  </tbody>
                </table>
                {#if Object.keys(room.cooling_table).length > 0}
                  <p class="hint">
                    Abkühlung am Soll je Außentemperatur:
                    {Object.entries(room.cooling_table)
                      .map(([outside, rate]) => `${outside} °C: ${formatNumber(rate, 2)} K/h`)
                      .join(' · ')}
                  </p>
                {/if}
              </details>

              {#if thermostat && thermostat.trv_entity && view.control && view.control.mode !== 'off'}
                <ThermostatPanel
                  room={thermostat}
                  appMode={view.control.app_mode}
                  probeHere={view.control.probe.room_id === room.entity_id &&
                    view.control.probe.flow_entity !== null &&
                    view.control.probe.return_entity !== null}
                  testStart={view.control.test_start}
                  onChange={(next) => (view = next)}
                />
              {/if}

              {#if editing === room.entity_id}
                <RoomEditor
                  entityId={room.entity_id}
                  name={room.name}
                  details={room.details}
                  atThermostat={room.measured_at_thermostat}
                  foundName={controlOf(room.entity_id)?.read?.found_name ?? null}
                  readsValve={(controlOf(room.entity_id)?.read ?? null) !== null}
                  {homeAssistantAvailable}
                  onSaved={(next) => {
                    view = next;
                    editing = null;
                  }}
                  onCancel={() => (editing = null)}
                />
              {:else}
                <div><button type="button" class="button small" onclick={() => startEdit(room.entity_id)}>Bearbeiten</button></div>
              {/if}
            </article>
          {/each}
        </div>
      </section>
    {/each}
  {/if}
</div>

<style>
  .page {
    display: flex;
    flex-direction: column;
    gap: 24px;
    min-width: 0;
  }

  .section-head h2 {
    font-size: 20px;
    font-weight: 600;
  }

  .zone {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .zone-head {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .zone-head h3 {
    font-size: 18px;
    font-weight: 600;
  }

  .rooms {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    gap: 24px;
    align-items: start;
  }

  .room-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
  }

  .room-head h4 {
    font-size: 17px;
    font-weight: 600;
  }

  .pills {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  dl.facts {
    margin: 0;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px 20px;
  }

  dl.facts > div {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  dl.facts dt {
    font-size: 13px;
    color: var(--text-secondary);
  }

  dl.facts dd {
    margin: 0;
    font-size: 16px;
    font-weight: 500;
  }

  details summary {
    cursor: pointer;
    min-height: 32px;
    font-size: 14px;
    color: var(--text-secondary);
  }

  table.learned {
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
    margin: 8px 0;
  }

  table.learned th,
  table.learned td {
    padding: 4px;
    text-align: left;
    border-bottom: 1px solid var(--divider);
  }

  table.learned th {
    font-weight: 500;
  }

  .link-button {
    display: inline-flex;
    align-items: center;
    text-decoration: none;
  }

  .button.small {
    min-height: 44px;
    padding: 0 14px;
    font-size: 14px;
  }

  @media (max-width: 420px) {
    .rooms {
      grid-template-columns: 1fr;
    }

    dl.facts {
      grid-template-columns: 1fr;
    }
  }
</style>
