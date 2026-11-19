<script lang="ts">
  import DumpButton from '../components/DumpButton.svelte';
  import HemsSettings from '../components/HemsSettings.svelte';
  import HomeAssistantSettings from '../components/HomeAssistantSettings.svelte';
  import MyVaillantChoice from '../components/MyVaillantChoice.svelte';
  import StatusPill from '../components/StatusPill.svelte';
  import { ApiError, saveConnection } from '../lib/api';
  import { CONNECTION_NAMES, PROJECT_LINKS, gatewayName, groupedSki, skiEnding } from '../lib/texts';
  import type { Connection, ConnectionStatus, HomeAssistantState, MyVaillantEntities, PairingStatus } from '../lib/types';

  let {
    connection,
    status,
    zones,
    homeAssistant,
    onRepair,
    onConnection,
    onRestart,
  }: {
    connection: ConnectionStatus;
    /** EEBUS only: the pairing; null with myVAILLANT. */
    status: PairingStatus | null;
    /** Zones the heat pump reports, for assigning rooms. */
    zones: { id: string; label: string }[];
    /** Live state of the connection to Home Assistant; null until the first update. */
    homeAssistant: HomeAssistantState | null;
    onRepair: () => void;
    onConnection: (status: ConnectionStatus) => void;
    /** The app restarts to work with this connection. */
    onRestart: (to: Connection) => void;
  } = $props();

  const gateway = $derived(status?.paired ?? null);
  const other = $derived<Connection>(connection.active === 'myvaillant' ? 'eebus' : 'myvaillant');
  // Switching asks first; to myVAILLANT it needs the entities.
  let switching = $state(false);
  let busy = $state(false);
  let error = $state<string | null>(null);
  let saved = $state(false);

  async function save(to: Connection, entities: MyVaillantEntities): Promise<void> {
    busy = true;
    error = null;
    saved = false;
    try {
      const next = await saveConnection(to, entities);
      onConnection(next);
      if (next.restarting) {
        onRestart(to);
      } else {
        saved = true;
      }
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Die Anbindung konnte nicht gespeichert werden.';
    } finally {
      busy = false;
    }
  }
</script>

<div class="page">
  <section class="card" aria-labelledby="connection-title">
    <div class="head">
      <h2 id="connection-title">Anbindung</h2>
      <StatusPill connection={connection.state} />
    </div>
    <p class="secondary">
      Die App erreicht die Wärmepumpe über <strong>{CONNECTION_NAMES[connection.active]}</strong>.
      {#if connection.active === 'myvaillant'}
        Die Werte kommen aus der Vaillant-Cloud und sind so alt wie die letzte Abfrage der Integration (in deren Optionen
        einstellbar; zu kurze Abstände sperrt die Cloud zeitweise).
      {/if}
    </p>
    {#if error}
      <p class="banner error" role="alert">{error}</p>
    {/if}
    {#if connection.active === 'myvaillant' && !switching}
      <MyVaillantChoice
        initial={connection.myvaillant}
        available={connection.home_assistant_available}
        {busy}
        submitLabel="Entitäten speichern"
        onSubmit={(entities) => void save('myvaillant', entities)}
      />
      {#if saved}
        <p class="hint" role="status">Gespeichert.</p>
      {/if}
    {/if}
    {#if switching}
      <div class="switch" role="group" aria-label="Anbindung wechseln">
        <p>
          Auf <strong>{CONNECTION_NAMES[other]}</strong> umstellen? Der Planer setzt seine Werte zurück, die App wechselt in
          den Demo-Betrieb und startet neu. Raumsensoren ordnen Sie danach den Zonen neu zu.
        </p>
        {#if other === 'myvaillant'}
          <MyVaillantChoice
            initial={connection.myvaillant}
            available={connection.home_assistant_available}
            {busy}
            submitLabel="Auf myVAILLANT umstellen"
            onSubmit={(entities) => void save('myvaillant', entities)}
          />
        {:else}
          <div>
            <button
              type="button"
              class="button primary"
              disabled={busy}
              onclick={() => void save('eebus', connection.myvaillant)}>Auf EEBUS umstellen</button
            >
          </div>
        {/if}
        <div>
          <button type="button" class="button" onclick={() => (switching = false)}>Abbrechen</button>
        </div>
      </div>
    {:else}
      <div>
        <button type="button" class="button" onclick={() => (switching = true)}
          >Auf {other === 'myvaillant' ? 'myVAILLANT' : 'EEBUS'} umstellen …</button
        >
      </div>
    {/if}
  </section>

  <div class="grid">
    {#if status}
    <section class="card" aria-labelledby="pairing-title">
      <div class="head">
        <h2 id="pairing-title">Kopplung</h2>
        <StatusPill connection={status.state} />
      </div>
      {#if status.state === 'trust_removed'}
        <p class="banner error" role="alert">
          Das Gateway hat das Vertrauen entzogen. Setzen Sie die App in der myVAILLANT-App unter Einstellungen → Netzwerk →
          EEBUS wieder auf „Vertrauenswürdig“.
        </p>
      {/if}
      <dl class="facts">
        {#if gateway}
          <div><dt>Gateway</dt><dd>{gatewayName(gateway)}</dd></div>
          <div><dt>Seriennummer</dt><dd>{gateway.serial || '—'}</dd></div>
          <div><dt>Software</dt><dd>{gateway.software_revision || '—'}</dd></div>
          <div><dt>Adresse im Netzwerk</dt><dd>{gateway.host || '—'}</dd></div>
          <div><dt>SKI des Gateways</dt><dd>endet auf {skiEnding(gateway.ski)}</dd></div>
        {/if}
        <div><dt>SKI dieser App</dt><dd><code>{status.bridge.ski ? groupedSki(status.bridge.ski) : '—'}</code></dd></div>
        <div><dt>EEBUS-Bridge</dt><dd>{status.bridge.reachable ? 'läuft' : 'nicht erreichbar'}</dd></div>
      </dl>
      <div>
        <button type="button" class="button" onclick={onRepair}>Anderes Gateway koppeln</button>
      </div>
    </section>

    <section class="card" aria-labelledby="diagnostics-title">
      <h2 id="diagnostics-title">Diagnose</h2>
      <p class="secondary">
        Der Diagnose-Dump beschreibt die vollständige EEBUS-Struktur Ihrer Anlage: Zonen, Messwerte, Sollwerte mit Grenzen
        und angebotene Betriebsarten. Die App liest dafür nur, sie ändert nichts an der Wärmepumpe. Der Dump enthält
        Modell- und Seriennummern Ihrer Geräte.
      </p>
      {#if status.state !== 'connected'}
        <p class="hint">Verfügbar, sobald das Gateway verbunden ist.</p>
      {/if}
      <DumpButton enabled={status.state === 'connected'} />
    </section>
    {/if}

    <section class="card" aria-labelledby="about-title">
      <h2 id="about-title">Über die App</h2>
      <dl class="facts">
        <div><dt>Version der App</dt><dd>{connection.app_version}</dd></div>
        {#if status}
          <div><dt>Version der EEBUS-Bridge</dt><dd>{status.bridge.build || '—'}</dd></div>
        {/if}
      </dl>
      <ul class="links">
        {#each PROJECT_LINKS as link (link.href)}
          <li><a href={link.href} target="_blank" rel="noopener noreferrer">{link.label}</a></li>
        {/each}
      </ul>
      <p class="hint">
        Die App steht unter der MIT-Lizenz. Sie enthält die EEBUS-Bridge aus
        <a href="https://github.com/volschin/eebus-ha-bridge" target="_blank" rel="noopener noreferrer"
          >volschin/eebus-ha-bridge</a
        > (MIT-Lizenz, Copyright © 2024 volschin).
      </p>
    </section>
  </div>

  <HomeAssistantSettings {zones} {homeAssistant} />

  <HemsSettings {zones} homeAssistantAvailable={homeAssistant?.available ?? false} />
</div>

<style>
  .page {
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

  .switch {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 16px;
    border: 1px solid var(--border);
    border-radius: 12px;
  }

  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  code {
    font-family: inherit;
    letter-spacing: 0.02em;
  }

  .links {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-wrap: wrap;
    gap: 8px 20px;
    font-size: 15px;
  }

  a {
    color: var(--info-text);
  }

  a:focus-visible {
    outline: 3px solid var(--info-text);
    outline-offset: 2px;
  }

  .links a {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    font-weight: 500;
  }

  @media (max-width: 420px) {
    .grid {
      grid-template-columns: 1fr;
    }
  }
</style>
