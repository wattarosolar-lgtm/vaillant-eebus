<script lang="ts">
  import { onMount } from 'svelte';

  import HeaderStatus from './components/HeaderStatus.svelte';
  import ModeBanner from './components/ModeBanner.svelte';
  import ModeSwitch from './components/ModeSwitch.svelte';
  import StatusPill from './components/StatusPill.svelte';
  import { ApiError, getPairing, setOperatingMode, subscribeState } from './lib/api';
  import { BRIDGE_PROBLEMS } from './lib/texts';
  import type { ApiState, OperatingMode, PairingStatus } from './lib/types';
  import Log from './pages/Log.svelte';
  import Overview from './pages/Overview.svelte';
  import Settings from './pages/Settings.svelte';
  import Setup from './pages/Setup.svelte';

  type Route = 'uebersicht' | 'einstellungen' | 'protokoll' | 'einrichtung';
  const ROUTES: Route[] = ['uebersicht', 'einstellungen', 'protokoll', 'einrichtung'];

  // The pairing status drives setup and settings; the heat pump values arrive
  // live over server-sent events. While connected the pairing rarely changes.
  const PAIRING_POLL_MS = 3000;
  const PAIRING_POLL_CONNECTED_MS = 30000;

  let pairing = $state<PairingStatus | null>(null);
  let live = $state<ApiState | null>(null);
  let streamOpen = $state(true);
  let loadError = $state<string | null>(null);
  let route = $state<Route | ''>(currentRoute());
  // Live mode is switched on only after the user confirmed it in the banner.
  let confirmingLive = $state(false);
  let modeBusy = $state(false);
  let modeError = $state<string | null>(null);

  function currentRoute(): Route | '' {
    const hash = window.location.hash.replace(/^#\/?/, '');
    return ROUTES.find((candidate) => candidate === hash) ?? '';
  }

  /** The new mode arrives with the next live update. */
  async function changeMode(mode: OperatingMode, confirm: boolean): Promise<void> {
    modeBusy = true;
    modeError = null;
    try {
      await setOperatingMode(mode, confirm);
      confirmingLive = false;
    } catch (err) {
      modeError = err instanceof ApiError ? err.message : 'Der Modus konnte nicht umgeschaltet werden.';
    } finally {
      modeBusy = false;
    }
  }

  function requestMode(mode: OperatingMode): void {
    if (mode === 'live') {
      confirmingLive = true;
    } else {
      confirmingLive = false;
      void changeMode('demo', false);
    }
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
    let timer: number | undefined;
    const poll = async (): Promise<void> => {
      await refreshPairing();
      const connected = pairing?.state === 'connected' && route !== 'einrichtung';
      timer = window.setTimeout(() => void poll(), connected ? PAIRING_POLL_CONNECTED_MS : PAIRING_POLL_MS);
    };
    void poll();
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
      window.clearTimeout(timer);
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
      <div class="header-right">
        {#if pairing?.paired}
          <ModeSwitch mode={live.mode} disabled={modeBusy} onRequest={requestMode} />
        {/if}
        <HeaderStatus {live} />
      </div>
    {:else if connection}
      <StatusPill {connection} />
    {/if}
  </header>

  {#if pairing?.paired && route !== 'einrichtung'}
    <nav aria-label="Seiten">
      <a href="#/uebersicht" aria-current={route === 'uebersicht' ? 'page' : undefined}>Übersicht</a>
      <a href="#/protokoll" aria-current={route === 'protokoll' ? 'page' : undefined}>Protokoll</a>
      <a href="#/einstellungen" aria-current={route === 'einstellungen' ? 'page' : undefined}>Einstellungen</a>
    </nav>
  {/if}

  {#if route === 'uebersicht' && live && pairing?.paired}
    <ModeBanner
      mode={live.mode}
      confirming={confirmingLive}
      busy={modeBusy}
      error={modeError}
      onConfirm={() => void changeMode('live', true)}
      onCancel={() => (confirmingLive = false)}
    />
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
      <Settings
        status={pairing}
        zones={live?.heat_pump?.zones ?? []}
        homeAssistant={live?.home_assistant ?? null}
        onRepair={() => navigate('einrichtung')}
      />
    {:else if route === 'protokoll'}
      <Log />
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

  .header-right {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
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
