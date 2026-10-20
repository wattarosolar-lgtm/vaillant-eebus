<script lang="ts">
  import DumpButton from '../components/DumpButton.svelte';
  import HomeAssistantSettings from '../components/HomeAssistantSettings.svelte';
  import StatusPill from '../components/StatusPill.svelte';
  import { gatewayName, groupedSki, skiEnding } from '../lib/texts';
  import type { PairingStatus } from '../lib/types';

  let {
    status,
    zones,
    onRepair,
  }: {
    status: PairingStatus;
    /** Zones the heat pump reports, for assigning rooms. */
    zones: { id: string; label: string }[];
    onRepair: () => void;
  } = $props();

  const gateway = $derived(status.paired);
  const bridgeText = $derived(
    status.bridge.reachable ? `läuft (Version ${status.bridge.build || 'unbekannt'})` : 'nicht erreichbar',
  );
</script>

<div class="grid">
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
      <div><dt>EEBUS-Bridge</dt><dd>{bridgeText}</dd></div>
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

  <HomeAssistantSettings {zones} />
</div>

<style>
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    gap: 24px;
    align-items: start;
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

  @media (max-width: 420px) {
    .grid {
      grid-template-columns: 1fr;
    }
  }
</style>
