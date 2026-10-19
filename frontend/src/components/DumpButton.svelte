<script lang="ts">
  import { downloadDump } from '../lib/api';

  let { enabled }: { enabled: boolean } = $props();

  let busy = $state(false);
  let error = $state<string | null>(null);

  async function download(): Promise<void> {
    busy = true;
    error = null;
    try {
      await downloadDump();
    } catch (err) {
      error = err instanceof Error ? err.message : 'Der Diagnose-Dump konnte nicht erstellt werden.';
    } finally {
      busy = false;
    }
  }
</script>

<div class="dump">
  <button type="button" class="button primary" onclick={download} disabled={!enabled || busy}>
    {busy ? 'Dump wird erstellt …' : 'Diagnose-Dump herunterladen'}
  </button>
  {#if busy}
    <p class="hint" role="status">Die App liest dafür alle Werte des Gateways. Das dauert bis zu 30 Sekunden.</p>
  {/if}
  {#if error}
    <p class="banner error" role="alert">{error}</p>
  {/if}
</div>

<style>
  .dump {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
</style>
