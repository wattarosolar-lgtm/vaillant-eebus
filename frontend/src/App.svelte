<script lang="ts">
  import { onMount } from 'svelte';

  import StatusPill from './components/StatusPill.svelte';
  import { ApiError, getPairing } from './lib/api';
  import type { PairingStatus } from './lib/types';
  import Settings from './pages/Settings.svelte';
  import Setup from './pages/Setup.svelte';

  // Live updates via server-sent events follow in phase 3; until then the
  // pairing status is polled.
  const POLL_INTERVAL_MS = 3000;

  let status = $state<PairingStatus | null>(null);
  let loadError = $state<string | null>(null);
  let route = $state(currentRoute());

  function currentRoute(): string {
    return window.location.hash.replace(/^#\/?/, '');
  }

  function navigate(target: 'einrichtung' | 'einstellungen'): void {
    window.location.hash = `#/${target}`;
  }

  async function refresh(): Promise<void> {
    try {
      status = await getPairing();
      loadError = null;
    } catch (err) {
      loadError = err instanceof ApiError ? err.message : 'Der Status der App konnte nicht geladen werden.';
    }
  }

  const showSetup = $derived(route === 'einrichtung');

  // Without a paired gateway the app starts in the setup wizard. Switching the
  // route keeps the wizard open after pairing until the user leaves it.
  $effect(() => {
    if (status !== null && status.paired === null && route === '') {
      navigate('einrichtung');
    }
  });

  onMount(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
    const onHashChange = (): void => {
      route = currentRoute();
    };
    window.addEventListener('hashchange', onHashChange);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('hashchange', onHashChange);
    };
  });
</script>

<div class="page">
  <header>
    <div class="title">
      <h1>Wärmepumpe</h1>
      <p class="secondary">Vaillant · lokal über EEBUS</p>
    </div>
    {#if status}
      <StatusPill connection={status.state} />
    {/if}
  </header>

  {#if loadError}
    <p class="banner error" role="alert">{loadError}</p>
  {/if}

  <main>
    {#if status === null}
      {#if !loadError}
        <p class="secondary" role="status">Status wird geladen …</p>
      {/if}
    {:else if showSetup}
      <Setup {status} onChange={(next) => (status = next)} onFinish={() => navigate('einstellungen')} />
    {:else}
      <Settings {status} onRepair={() => navigate('einrichtung')} />
    {/if}
  </main>
</div>

<style>
  .page {
    max-width: 1360px;
    margin: 0 auto;
    padding: 32px 32px 48px;
    display: flex;
    flex-direction: column;
    gap: 24px;
    box-sizing: border-box;
  }

  header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }

  .title {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  h1 {
    font-size: 28px;
    font-weight: 600;
    letter-spacing: -0.01em;
  }

  .title .secondary {
    font-size: 14px;
  }

  @media (max-width: 600px) {
    .page {
      padding: 16px 16px 32px;
    }
  }
</style>
