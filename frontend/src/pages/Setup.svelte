<script lang="ts">
  import EebusSetup from '../components/EebusSetup.svelte';
  import MyVaillantChoice from '../components/MyVaillantChoice.svelte';
  import { ApiError, saveConnection } from '../lib/api';
  import type { Connection, ConnectionStatus, MyVaillantEntities, PairingStatus } from '../lib/types';

  let {
    connection,
    pairing,
    onConnection,
    onPairing,
    onRestart,
    onFinish,
  }: {
    connection: ConnectionStatus;
    /** EEBUS only; null while it loads or with myVAILLANT. */
    pairing: PairingStatus | null;
    onConnection: (status: ConnectionStatus) => void;
    onPairing: (status: PairingStatus) => void;
    /** The app restarts to work with this connection. */
    onRestart: (to: Connection) => void;
    onFinish: () => void;
  } = $props();

  // A new installation chooses first; an installation that chose already goes on with it.
  // svelte-ignore state_referenced_locally
  let step = $state<'choose' | 'myvaillant' | 'eebus'>(connection.connection ?? 'choose');
  // svelte-ignore state_referenced_locally
  let choice = $state<Connection>(connection.connection ?? 'myvaillant');
  let busy = $state(false);
  let error = $state<string | null>(null);

  async function save(chosen: Connection, entities: MyVaillantEntities): Promise<void> {
    busy = true;
    error = null;
    try {
      const status = await saveConnection(chosen, entities);
      onConnection(status);
      if (status.restarting) {
        onRestart(chosen);
      } else if (chosen === 'myvaillant') {
        onFinish();
      } else {
        step = 'eebus';
      }
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Die Anbindung konnte nicht gespeichert werden.';
    } finally {
      busy = false;
    }
  }

  function next(): void {
    if (choice === 'myvaillant') {
      step = 'myvaillant';
    } else {
      void save('eebus', connection.myvaillant);
    }
  }
</script>

<section class="setup" aria-labelledby="setup-title">
  <h2 id="setup-title">Einrichtung</h2>

  {#if error}
    <p class="banner error" role="alert">{error}</p>
  {/if}

  {#if step === 'choose'}
    <div class="card">
      <h3>Wie erreicht die App die Wärmepumpe?</h3>
      <fieldset class="options">
        <legend class="sr-only">Anbindung</legend>
        <label class="option" class:selected={choice === 'myvaillant'}>
          <input type="radio" name="connection" value="myvaillant" bind:group={choice} />
          <span>
            <strong>myVAILLANT in Home Assistant</strong>
            <span class="secondary">
              Die App liest und steuert die Wärmepumpe über die Entitäten der myVAILLANT-Integration. Die Integration muss
              in Home Assistant eingerichtet sein. Die Werte kommen aus der Vaillant-Cloud und sind so alt wie die letzte
              Abfrage der Integration. Im Auto-Betrieb wirkt eine Soll-Änderung als zeitweise Übersteuerung.
            </span>
          </span>
        </label>
        <label class="option" class:selected={choice === 'eebus'}>
          <input type="radio" name="connection" value="eebus" bind:group={choice} />
          <span>
            <strong>EEBUS im Heimnetz</strong>
            <span class="secondary">
              Die App verbindet sich ohne Cloud direkt mit dem Gateway der Wärmepumpe (VR921 oder VR940f). Dafür wird sie
              einmal in der myVAILLANT-App gekoppelt. Die Werte kommen jede Minute; im Auto-Betrieb ist die
              Solltemperatur nicht änderbar.
            </span>
          </span>
        </label>
      </fieldset>
      <p class="hint">Die Anbindung lässt sich später in den Einstellungen wechseln.</p>
      <div>
        <button type="button" class="button primary" onclick={next} disabled={busy}>Weiter</button>
      </div>
    </div>
  {:else if step === 'myvaillant'}
    <div class="card">
      <h3>myVAILLANT-Entitäten wählen</h3>
      <MyVaillantChoice
        initial={connection.myvaillant}
        available={connection.home_assistant_available}
        {busy}
        submitLabel="Übernehmen"
        onSubmit={(entities) => void save('myvaillant', entities)}
      />
      <div>
        <button type="button" class="button" onclick={() => (step = 'choose')}>Andere Anbindung wählen</button>
      </div>
    </div>
  {:else if pairing === null}
    <p class="secondary" role="status">Status wird geladen …</p>
  {:else}
    <EebusSetup status={pairing} onChange={onPairing} {onFinish} />
    <div>
      <button type="button" class="button" onclick={() => (step = 'choose')}>Andere Anbindung wählen</button>
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

  h2 {
    font-size: 22px;
    font-weight: 600;
  }

  h3 {
    font-size: 18px;
    font-weight: 600;
  }

  .options {
    margin: 0;
    padding: 0;
    border: 0;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .option {
    display: flex;
    gap: 12px;
    align-items: flex-start;
    padding: 16px;
    border: 1px solid var(--border);
    border-radius: 12px;
    cursor: pointer;
  }

  .option.selected {
    border-color: var(--text);
  }

  .option input {
    margin-top: 4px;
    width: 18px;
    height: 18px;
    flex: none;
  }

  .option > span {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 15px;
  }
</style>
