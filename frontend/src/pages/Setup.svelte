<script lang="ts">
  import DumpButton from '../components/DumpButton.svelte';
  import StatusPill from '../components/StatusPill.svelte';
  import { ApiError, registerGateway } from '../lib/api';
  import { BRIDGE_PROBLEMS, gatewayDetails, gatewayName, groupedSki, skiEnding } from '../lib/texts';
  import type { ConnectionState, PairingStatus } from '../lib/types';

  let {
    status,
    onChange,
    onFinish,
  }: {
    status: PairingStatus;
    onChange: (status: PairingStatus) => void;
    onFinish: () => void;
  } = $props();

  const STEPS = ['Gateway auswählen', 'In myVAILLANT bestätigen', 'Fertig'];

  const WAITING_TEXT: Record<ConnectionState, string> = {
    not_paired: '',
    disconnected:
      'Warte auf das Gateway. Sobald Sie die App in myVAILLANT bestätigt haben, verbindet sie sich von selbst.',
    connected: 'Verbunden.',
    trust_removed:
      'Das Gateway hat die Verbindung abgelehnt. Prüfen Sie, ob die App in myVAILLANT als vertrauenswürdig markiert ist.',
    failed: 'Die Verbindung ist gestört. Die Bridge versucht es weiter; ein Neustart der App kann helfen.',
    bridge_unavailable: 'Die EEBUS-Bridge der App ist gerade nicht erreichbar und startet neu.',
    ship_port_in_use: 'Die EEBUS-Bridge der App kann nicht starten (siehe Hinweis oben).',
    foreign_bridge: 'Die App spricht nicht mit ihrer eigenen EEBUS-Bridge (siehe Hinweis oben).',
  };

  let chosenSki = $state<string | null>(null);
  let pairingSki = $state<string | null>(null);
  let error = $state<string | null>(null);

  const step = $derived(chosenSki === null ? 1 : status.state === 'connected' ? 3 : 2);

  async function pair(ski: string): Promise<void> {
    pairingSki = ski;
    error = null;
    try {
      onChange(await registerGateway(ski));
      chosenSki = ski;
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Die Kopplung ist fehlgeschlagen.';
    } finally {
      pairingSki = null;
    }
  }
</script>

<section class="setup" aria-labelledby="setup-title">
  <div class="intro">
    <h2 id="setup-title">Wärmepumpe koppeln</h2>
    <ol class="steps">
      {#each STEPS as label, index (label)}
        <li class:done={index + 1 < step} class:current={index + 1 === step} aria-current={index + 1 === step ? 'step' : undefined}>
          <span class="number">{index + 1}</span>{label}
        </li>
      {/each}
    </ol>
  </div>

  {#if error}
    <p class="banner error" role="alert">{error}</p>
  {/if}

  {#if step === 1}
    <div class="card">
      <h3>Gateway auswählen</h3>
      <p class="secondary">
        Die App sucht im Netzwerk nach EEBUS-Geräten. Wählen Sie das Gateway Ihrer Wärmepumpe (VR921 oder VR940f). Andere
        EEBUS-Geräte im Netz, etwa Wechselrichter, Wallboxen oder Energiemanager, erscheinen hier ebenfalls.
      </p>
      {#if BRIDGE_PROBLEMS[status.state]}
        <p class="secondary">Die Suche startet, sobald das Problem oben behoben ist.</p>
      {:else if status.discovered.length === 0}
        <p class="banner info" role="status">Noch kein Gerät gefunden. Die Suche läuft weiter …</p>
      {:else}
        <ul class="gateways">
          {#each status.discovered as gateway (gateway.ski)}
            <li class="gateway">
              <div class="gateway-text">
                <p class="name">{gatewayName(gateway)}</p>
                {#if gatewayDetails(gateway)}
                  <p class="hint">{gatewayDetails(gateway)}</p>
                {/if}
                <p class="hint">SKI endet auf {skiEnding(gateway.ski)}</p>
              </div>
              <button
                type="button"
                class="button primary"
                onclick={() => pair(gateway.ski)}
                disabled={pairingSki !== null}
              >
                {#if pairingSki === gateway.ski}
                  Wird gekoppelt …
                {:else if status.paired?.ski === gateway.ski}
                  Weiter mit diesem Gateway
                {:else}
                  Dieses Gateway koppeln
                {/if}
              </button>
            </li>
          {/each}
        </ul>
      {/if}
      <details>
        <summary>Das Gateway wird nicht gefunden?</summary>
        <ul class="secondary">
          <li>
            Gateway und Home Assistant müssen im selben Netzwerk sein. Die Suche läuft über mDNS; getrennte Netze oder
            VLANs blockieren sie.
          </li>
          <li>
            Das Gateway erlaubt nur eine EEBUS-Verbindung. Ist bereits ein anderer EEBUS-Partner gekoppelt, zum Beispiel
            ein Energiemanager oder eine andere EEBUS-App, entfernen Sie ihn zuerst.
          </li>
          <li>Nach dem Einschalten des Gateways kann es einige Minuten dauern, bis es sich meldet.</li>
        </ul>
      </details>
    </div>
  {:else if step === 2}
    <div class="card">
      <h3>In der myVAILLANT-App bestätigen</h3>
      <ol class="instructions">
        <li>Öffnen Sie die myVAILLANT-App.</li>
        <li>Gehen Sie zu <strong>Einstellungen → Netzwerk → EEBUS</strong>.</li>
        <li>Setzen Sie diese App unter den verfügbaren Geräten auf <strong>Vertrauenswürdig</strong>.</li>
      </ol>
      <dl class="facts">
        <div>
          <dt>Kennung dieser App (SKI)</dt>
          <dd><code>{groupedSki(status.bridge.ski)}</code></dd>
        </div>
        <div>
          <dt>Endet auf</dt>
          <dd>{skiEnding(status.bridge.ski)}</dd>
        </div>
      </dl>
      <div class="live" role="status" aria-live="polite">
        <StatusPill connection={status.state} />
        <span class="secondary">{WAITING_TEXT[status.state]}</span>
      </div>
      <details>
        <summary>Es kommt keine Verbindung zustande?</summary>
        <ul class="secondary">
          <li>
            Das Gateway erlaubt nur eine EEBUS-Verbindung. Ist bereits ein anderer EEBUS-Partner gekoppelt, kommt keine
            Verbindung zustande.
          </li>
          <li>Prüfen Sie, ob die App in myVAILLANT wirklich als „Vertrauenswürdig“ markiert ist.</li>
          <li>
            Für die Fehlersuche: Stellen Sie in den App-Optionen die Log-Stufe auf „debug“. Das Log zeigt dann den
            Verbindungsaufbau mit dem Gateway im Detail.
          </li>
        </ul>
      </details>
      <div class="actions">
        <button type="button" class="button" onclick={() => (chosenSki = null)}>Anderes Gateway wählen</button>
      </div>
    </div>
  {:else}
    <div class="card">
      <h3>Verbunden</h3>
      <p class="secondary">
        Die App ist mit {status.paired ? gatewayName(status.paired) : 'dem Gateway'} gekoppelt. Sie liest ab jetzt die Werte
        der Wärmepumpe; gesendet wird nichts.
      </p>
      <p class="secondary">
        Bitte laden Sie jetzt den Diagnose-Dump herunter. Er beschreibt, was Ihre Anlage über EEBUS anbietet, und wird für
        die nächsten Schritte der Einrichtung ausgewertet. Er enthält Modell- und Seriennummern Ihrer Geräte.
      </p>
      <DumpButton enabled={true} />
      <div class="actions">
        <button type="button" class="button" onclick={onFinish}>Weiter zur Übersicht</button>
      </div>
    </div>
  {/if}
</section>

<style>
  .setup {
    max-width: 760px;
    display: flex;
    flex-direction: column;
    gap: 24px;
  }

  .intro {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .intro h2 {
    font-size: 22px;
    font-weight: 600;
  }

  h3 {
    font-size: 18px;
    font-weight: 600;
  }

  .steps {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-wrap: wrap;
    gap: 8px 20px;
    font-size: 14px;
    color: var(--text-hint);
  }

  .steps li {
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }

  .steps .number {
    display: inline-grid;
    place-items: center;
    width: 26px;
    height: 26px;
    border-radius: 999px;
    border: 1px solid var(--control-border);
    background: var(--surface);
    font-weight: 600;
  }

  .steps .current {
    color: var(--text);
    font-weight: 600;
  }

  .steps .current .number {
    background: var(--text);
    border-color: var(--text);
    color: #ffffff;
  }

  .steps .done .number {
    background: var(--ok-bg);
    border-color: var(--ok-bg);
    color: var(--ok-text);
  }

  .gateways {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .gateway {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 16px;
    border: 1px solid var(--border);
    border-radius: 12px;
  }

  .gateway-text {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .name {
    font-size: 16px;
    font-weight: 600;
  }

  .instructions {
    margin: 0;
    padding-left: 20px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 15px;
  }

  .live {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
  }

  details {
    font-size: 15px;
  }

  summary {
    cursor: pointer;
    color: var(--text-secondary);
    /* list-item keeps the disclosure triangle; padding gives a 44 px target. */
    padding: 11px 0;
  }

  details ul {
    margin: 0;
    padding-left: 20px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  code {
    font-family: inherit;
    letter-spacing: 0.02em;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
</style>
