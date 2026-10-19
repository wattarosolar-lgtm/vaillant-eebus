<script lang="ts">
  import Dial from '../components/Dial.svelte';
  import { kilowattHours, kilowatts, temperature, timeOfDay, watts } from '../lib/format';
  import { BOOST_LABELS, BRIDGE_PROBLEMS } from '../lib/texts';
  import type { ApiState, Reading } from '../lib/types';

  let { live, streamOpen }: { live: ApiState | null; streamOpen: boolean } = $props();

  const NOT_AVAILABLE = 'nicht verfügbar';

  const heatPump = $derived(live?.heat_pump ?? null);
  // Values are only shown while they are current: snapshot readable and gateway connected.
  const values = $derived(heatPump && heatPump.available && heatPump.connected ? heatPump : null);
  const measurements = $derived(values?.measurements ?? {});

  function celsius(reading: Reading | null | undefined): string | null {
    const value = temperature(reading);
    return value === null ? null : `${value} °C`;
  }

  const energyRows = $derived([
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
          <Dial
            title={zone.label}
            label="Raumtemperatur"
            current={temperature(zone.room_temperature)}
            subline={zone.room_temperature ? 'Raumfühler (EEBUS)' : 'Kommt später aus Home Assistant'}
            setpoint={zone.setpoint}
            mode={zone.mode}
            availableModes={zone.available_modes}
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
            mode={values.hot_water.mode}
            availableModes={values.hot_water.available_modes}
            color="var(--water)"
            textColor="var(--water-text)"
          >
            {#if values.hot_water.boost}
              <div class="boost">
                <div class="boost-text">
                  <span class="boost-title">Einmalladung</span>
                  <span class="hint">Speicher einmal aufheizen</span>
                </div>
                <span class="chip" class:running={values.hot_water.boost === 'running' || values.hot_water.boost === 'active'}>
                  {BOOST_LABELS[values.hot_water.boost]}
                </span>
              </div>
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

  .boost {
    align-self: stretch;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding-top: 4px;
  }

  .boost-text {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .boost-title {
    font-size: 15px;
    font-weight: 500;
  }

  .chip {
    padding: 4px 10px;
    border-radius: 999px;
    background: var(--bg);
    color: var(--text-secondary);
    font-size: 13px;
    font-weight: 500;
  }

  .chip.running {
    background: var(--water);
    color: #ffffff;
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
