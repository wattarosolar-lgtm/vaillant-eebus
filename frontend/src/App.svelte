<script lang="ts">
  import { onMount } from 'svelte';

  import HeaderStatus from './components/HeaderStatus.svelte';
  import StatusPill from './components/StatusPill.svelte';
  import { ApiError, getPairing, subscribeState } from './lib/api';
  import { BRIDGE_PROBLEMS } from './lib/texts';
  import type { ApiState, PairingStatus } from './lib/types';
  import Overview from './pages/Overview.svelte';
  import Settings from './pages/Settings.svelte';
  import Setup from './pages/Setup.svelte';

  type Route = 'uebersicht' | 'einstellungen' | 'einrichtung';

  // The pairing status drives setup and settings; the heat pump values arrive
  // live over server-sent events.
  const PAIRING_POLL_MS = 3000;

  let pairing = $state<PairingStatus | null>(null);
  let live = $state<ApiState | null>(null);
  let streamOpen = $state(true);
  let loadError = $state<string | null>(null);
  let route = $state<Route | ''>(currentRoute());

  function currentRoute(): Route | '' {
    const hash = window.location.hash.replace(/^#\/?/, '');
    return hash === 'uebersicht' || hash === 'einstellungen' || hash === 'einrichtung' ? hash : '';
  }

  function navigate(target: Route): void {
    window.location.hash = `#/${target}`;
  }

  async function refreshPairing(): Promise<void> {
    try {
      pairing = await getPairing();
      loadError = null;
    } catch (err) {
      loadError = err instanceof ApiError ? err.message : 'Der Status der App konnte nicht geladen werden.';
    }
  }

  // Start page: the setup wizard without a paired gateway, the overview otherwise.
  // Switching the route keeps the wizard open after pairing until the user leaves it.
  $effect(() => {
    if (pairing !== null && route === '') {
      navigate(pairing.paired === null ? 'einrichtung' : 'uebersicht');
    }
  });

  const connection = $derived(live?.connection ?? pairing?.state ?? null);
  const problem = $derived(connection ? BRIDGE_PROBLEMS[connection] : undefined);

  onMount(() => {
    void refreshPairing();
    const timer = window.setInterval(() => void refreshPairing(), PAIRING_POLL_MS);
    const closeStream = subscribeState(
      (state) => {
        live = state;
      },
      (open) => {
        streamOpen = open;
      },
    );
    const onHashChange = (): void => {
      route = currentRoute();
    };
    window.addEventListener('hashchange', onHashChange);
    return () => {
      window.clearInterval(timer);
      closeStream();
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
    {#if route === 'uebersicht' && live}
      <HeaderStatus {live} />
    {:else if connection}
      <StatusPill {connection} />
    {/if}
  </header>

  {#if pairing?.paired && route !== 'einrichtung'}
    <nav aria-label="Seiten">
      <a href="#/uebersicht" aria-current={route === 'uebersicht' ? 'page' : undefined}>Übersicht</a>
      <a href="#/einstellungen" aria-current={route === 'einstellungen' ? 'page' : undefined}>Einstellungen</a>
    </nav>
  {/if}

  {#if loadError}
    <p class="banner error" role="alert">{loadError}</p>
  {:else if problem}
    <p class="banner error" role="alert">{problem}</p>
  {/if}

  <main>
    {#if pairing === null}
      {#if !loadError}
        <p class="secondary" role="status">Status wird geladen …</p>
      {/if}
    {:else if route === 'einrichtung'}
      <Setup status={pairing} onChange={(next) => (pairing = next)} onFinish={() => navigate('uebersicht')} />
    {:else if route === 'einstellungen'}
      <Settings status={pairing} onRepair={() => navigate('einrichtung')} />
    {:else}
      <Overview {live} {streamOpen} />
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

  nav {
    display: flex;
    gap: 4px;
    padding: 4px;
    background: var(--segment-bg);
    border-radius: 12px;
    align-self: flex-start;
  }

  nav a {
    min-height: 40px;
    padding: 0 16px;
    display: inline-flex;
    align-items: center;
    border-radius: 9px;
    color: var(--text-secondary);
    font-size: 14px;
    font-weight: 500;
    text-decoration: none;
  }

  nav a[aria-current='page'] {
    background: var(--surface);
    color: var(--text);
    font-weight: 600;
  }

  nav a:focus-visible {
    outline: 3px solid var(--info-text);
    outline-offset: 2px;
  }

  @media (max-width: 600px) {
    .page {
      padding: 16px 16px 32px;
    }
  }
</style>
