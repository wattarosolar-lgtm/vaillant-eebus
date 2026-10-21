<script lang="ts">
  import BoostSwitch from '../components/BoostSwitch.svelte';
  import Dial from '../components/Dial.svelte';
  import HemsCard from '../components/HemsCard.svelte';
  import PowerChart from '../components/PowerChart.svelte';
  import {
    setHotWaterBoost,
    setHotWaterMode,
    setHotWaterSetpoint,
    setZoneMode,
    setZoneSetpoint,
  } from '../lib/api';
  import {
    energyToday,
    formatNumber,
    kilowattHours,
    kilowatts,
    temperature,
    timeOfDay,
    watts,
  } from '../lib/format';
  import { AUTO_MODE_SETPOINT_LOCKED, BRIDGE_PROBLEMS, VALUE_STATUS_LABELS } from '../lib/texts';
  import type { ApiState, Control, Reading, RoomReading } from '../lib/types';

  let { live, streamOpen }: { live: ApiState | null; streamOpen: boolean } = $props();

  const NOT_AVAILABLE = 'nicht verfügbar';

  const heatPump = $derived(live?.heat_pump ?? null);
  // Values are only shown while they are current: snapshot readable and gateway connected.
  const values = $derived(heatPump && heatPump.available && heatPump.connected ? heatPump : null);
  const measurements = $derived(values?.measurements ?? {});

  /** The value sent for a control that the device has not reported yet. */
  function pending(control: Control, zoneId: string | null = null): number | string | boolean | null {
    return live?.pending.find((write) => write.control === control && write.zone_id === zoneId)?.value ?? null;
  }

  function celsius(reading: Reading | null | undefined): string | null {
    const value = temperature(reading);
    return value === null ? null : `${value} °C`;
  }

  const homeAssistant = $derived(live?.home_assistant ?? null);

  function rooms(count: number): string {
    return count === 1 ? '1 Raum' : `${count} Räumen`;
  }

  /** The zone's room temperature: the average of its rooms in Home Assistant. */
  function zoneRoom(zoneId: string): { current: string | null; subline: string } {
    if (!homeAssistant?.available) {
      return { current: null, subline: 'Kommt aus Home Assistant' };
    }
    const average = homeAssistant.zones.find((zone) => zone.zone_id === zoneId);
    if (!average) {
      return { current: null, subline: 'Räume in den Einstellungen wählen' };
    }
    if (!homeAssistant.connected) {
      return { current: null, subline: 'Home Assistant nicht erreichbar' };
    }
    if (average.celsius === null) {
      return { current: null, subline: `Keine aktuellen Werte (${rooms(average.of)})` };
    }
    const used = average.rooms === average.of ? rooms(average.of) : `${average.rooms} von ${rooms(average.of)}`;
    return { current: formatNumber(average.celsius, 1), subline: `Mittelwert aus ${used}` };
  }

  function zoneName(zoneId: string | null): string {
    if (zoneId === null) {
      return 'keine Zone';
    }
    return values?.zones.find((zone) => zone.id === zoneId)?.label ?? `Zone ${zoneId}`;
  }

  function roomValue(room: RoomReading): string {
    if (room.celsius === null || room.status === 'unavailable') {
      return NOT_AVAILABLE;
    }
    const value = `${formatNumber(room.celsius, 1)} °C`;
    return room.status === 'stale' ? `${value} (${VALUE_STATUS_LABELS.stale})` : value;
  }

  const heatRows = $derived.by(() => {
    const heat = homeAssistant?.heat_today;
    if (!heat) {
      return [];
    }
    const kwh =
      heat.kwh === null || heat.status === 'unavailable'
        ? null
        : `${formatNumber(heat.kwh, 2)} kWh${heat.status === 'stale' ? ` (${VALUE_STATUS_LABELS.stale})` : ''}`;
    const performance = live?.performance_today ?? null;
    return [
      ['Wärme heute (aus Home Assistant)', kwh],
      ['Arbeitszahl heute', performance === null ? null : formatNumber(performance, 2)],
    ] as const;
  });

  const energyRows = $derived([
    ['Energie heute (berechnet)', energyToday(live?.energy_today)],
    ...heatRows,
    ['Verdichterleistung', watts(measurements['compressor_power'])],
    ['Energie gesamt', kilowattHours(measurements['energy_consumed'])],
    ['Erzeugte Energie', kilowattHours(measurements['energy_produced'])],
    ['davon Heizung', kilowattHours(measurements['energy_consumed_heating'])],
    ['davon Warmwasser', kilowattHours(measurements['energy_consumed_dhw'])],
  ] as const);

  const temperatureRows = $derived([
    ['Außen', celsius(measurements['outdoor_temperature'])],
    ['Vorlauf', celsius(measurements['flow_temperature'])],
    ['Rücklauf', celsius(measurements['return_temperature'])],
    ['Verdichter', celsius(measurements['compressor_temperature'])],
    ['Warmwasser', celsius(measurements['dhw_temperature'])],
  ] as const);
</script>

{#if live === null}
  <p class="secondary" role="status">Werte werden geladen …</p>
{:else if live.connection === 'not_paired'}
  <section class="card">
    <h2>Noch kein Gateway gekoppelt</h2>
    <p class="secondary">Koppeln Sie zuerst das Gateway Ihrer Wärmepumpe.</p>
    <div><a class="button primary link-button" href="#/einrichtung">Zur Einrichtung</a></div>
  </section>
{:else if !BRIDGE_PROBLEMS[live.connection]}
  <div class="overview">
    {#if !streamOpen}
      <p class="banner warn" role="status">Die Live-Verbindung zur App ist unterbrochen und wird wiederhergestellt …</p>
    {/if}
    {#if heatPump && !heatPump.available}
      <p class="banner warn" role="status">
        Die Werte der Wärmepumpe sind gerade nicht abrufbar. Die App versucht es weiter.
      </p>
    {:else if heatPump && !heatPump.connected}
      <p class="banner warn" role="status">
        Das Gateway ist gerade getrennt. Sobald es wieder verbunden ist, erscheinen die Werte.
      </p>
    {:else if heatPump === null}
      <p class="secondary" role="status">Werte werden geladen …</p>
    {/if}

    {#if values}
      <section class="grid" aria-label="Heizzonen und Warmwasser">
        {#each values.zones as zone (zone.id)}
          <!-- The room temperature is the average of the zone's rooms in Home
               Assistant; the gateway's own value is unreliable (field finding). -->
          {@const room = zoneRoom(zone.id)}
          <Dial
            title={zone.label}
            label="Raumtemperatur"
            current={room.current}
            subline={room.subline}
            setpoint={zone.setpoint}
            operationMode={zone.mode}
            availableModes={zone.available_modes}
            modeWritable={zone.mode_writable}
            appMode={live.mode}
            pendingSetpoint={pending('zone_setpoint', zone.id) as number | null}
            pendingMode={pending('zone_mode', zone.id) as string | null}
            setpointLocked={zone.mode === 'auto' ? AUTO_MODE_SETPOINT_LOCKED : null}
            sendSetpoint={(value, shown) => setZoneSetpoint(zone.id, value, shown)}
            sendMode={(mode, shown) => setZoneMode(zone.id, mode, shown)}
            color="var(--heat)"
            textColor="var(--heat-text)"
          />
        {/each}
        {#if values.hot_water}
          <Dial
            title="Warmwasser"
            label="Speichertemperatur"
            current={temperature(values.hot_water.temperature)}
            subline="Speicher"
            setpoint={values.hot_water.setpoint}
            operationMode={values.hot_water.mode}
            availableModes={values.hot_water.available_modes}
            modeWritable={values.hot_water.mode_writable}
            appMode={live.mode}
            pendingSetpoint={pending('dhw_setpoint') as number | null}
            pendingMode={pending('dhw_mode') as string | null}
            sendSetpoint={setHotWaterSetpoint}
            sendMode={setHotWaterMode}
            color="var(--water)"
            textColor="var(--water-text)"
          >
            {#if values.hot_water.boost}
              <BoostSwitch
                boost={values.hot_water.boost}
                writable={values.hot_water.boost_writable}
                mode={live.mode}
                pending={pending('dhw_boost') as boolean | null}
                send={setHotWaterBoost}
              />
            {/if}
          </Dial>
        {/if}
      </section>

      <section class="grid" aria-label="Messwerte">
        <article class="card">
          <h2>Leistung und Energie</h2>
          <div class="big">
            <span class="big-value">{kilowatts(measurements['power_consumption']) ?? '—'}</span>
            <span class="big-unit">kW aktuell</span>
          </div>
          <PowerChart />
          <dl class="pairs">
            {#each energyRows as [label, value] (label)}
              <div><dt>{label}</dt><dd class:na={value === null}>{value ?? NOT_AVAILABLE}</dd></div>
            {/each}
          </dl>
        </article>

        <article class="card">
          <h2>Temperaturen</h2>
          <dl class="facts">
            {#each temperatureRows as [label, value] (label)}
              <div><dt>{label}</dt><dd class:na={value === null}>{value ?? NOT_AVAILABLE}</dd></div>
            {/each}
          </dl>
        </article>

        {#if homeAssistant && homeAssistant.rooms.length > 0}
          <article class="card">
            <div class="card-head">
              <h2>Innentemperaturen</h2>
              <span class="hint">aus Home Assistant</span>
            </div>
            {#if !homeAssistant.connected}
              <p class="hint" role="status">Home Assistant ist gerade nicht erreichbar.</p>
            {/if}
            <dl class="facts">
              {#each homeAssistant.rooms as room (room.entity_id)}
                <div>
                  <dt>{room.name} <span class="hint">· {zoneName(room.zone_id)}</span></dt>
                  <dd class:na={room.status !== 'ok'}>{roomValue(room)}</dd>
                </div>
              {/each}
              {#each homeAssistant.zones as zone (zone.zone_id)}
                <div class="average">
                  <dt>Mittelwert {zoneName(zone.zone_id)}</dt>
                  <dd class:na={zone.celsius === null}>
                    {zone.celsius === null ? NOT_AVAILABLE : `${formatNumber(zone.celsius, 1)} °C`}
                  </dd>
                </div>
              {/each}
            </dl>
          </article>
        {/if}
      </section>

      <!-- Full width: the timeline needs the room. -->
      <section aria-label="Energiemanagement">
        <HemsCard />
      </section>

      {#if values.captured_at}
        <p class="hint">Stand {timeOfDay(values.captured_at)} Uhr</p>
      {/if}
    {/if}
  </div>
{/if}

<style>
  .overview {
    display: flex;
    flex-direction: column;
    gap: 24px;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    gap: 24px;
    align-items: start;
  }

  .big {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }

  .big-value {
    font-size: 44px;
    font-weight: 600;
    letter-spacing: -0.02em;
  }

  .big-unit {
    font-size: 18px;
    color: var(--text-secondary);
  }

  dl.pairs {
    margin: 0;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px 24px;
  }

  dl.pairs > div {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  dl.pairs dt {
    font-size: 13px;
    color: var(--text-secondary);
  }

  dl.pairs dd {
    margin: 0;
    font-size: 18px;
    font-weight: 500;
  }

  dd.na {
    color: var(--text-hint);
    font-size: 15px;
    font-weight: 400;
  }

  .card-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
  }

  /* Room names may be long; they wrap instead of pushing the value out. */
  dl.facts dt {
    flex-shrink: 1;
  }

  .average dt,
  .average dd:not(.na) {
    color: var(--text);
    font-weight: 600;
  }

  .link-button {
    display: inline-flex;
    align-items: center;
    text-decoration: none;
  }

  @media (max-width: 420px) {
    .grid {
      grid-template-columns: 1fr;
    }
  }
</style>
