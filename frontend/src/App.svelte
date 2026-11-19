<script lang="ts">
  import { onMount } from 'svelte';

  import HeaderStatus from './components/HeaderStatus.svelte';
  import ModeBanner from './components/ModeBanner.svelte';
  import ModeSwitch from './components/ModeSwitch.svelte';
  import StatusPill from './components/StatusPill.svelte';
  import { ApiError, getConnection, getPairing, setOperatingMode, subscribeState } from './lib/api';
  import { BRIDGE_PROBLEMS, CONNECTION_SUBTITLES } from './lib/texts';
  import type { ApiState, Connection, ConnectionStatus, OperatingMode, PairingStatus } from './lib/types';
  import Log from './pages/Log.svelte';
  import Overview from './pages/Overview.svelte';
  import Settings from './pages/Settings.svelte';
  import Setup from './pages/Setup.svelte';

  type Route = 'uebersicht' | 'einstellungen' | 'protokoll' | 'einrichtung';
  const ROUTES: Route[] = ['uebersicht', 'einstellungen', 'protokoll', 'einrichtung'];

  // The connection (and with EEBUS the pairing) drives setup and settings; the heat
  // pump values arrive live over server-sent events. While connected they rarely change.
  const PAIRING_POLL_MS = 3000;
  const PAIRING_POLL_CONNECTED_MS = 30000;
  // While the app restarts for another connection: how often to ask, and for how long.
  const RESTART_POLL_MS = 1500;
  const RESTART_GIVE_UP_MS = 90000;

  let connection = $state<ConnectionStatus | null>(null);
  let pairing = $state<PairingStatus | null>(null);
  let restarting = $state<Connection | null>(null);
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

  async function refreshStatus(): Promise<void> {
    if (restarting !== null) {
      return;
    }
    try {
      const next = await getConnection();
      pairing = next.active === 'eebus' ? await getPairing() : null;
      connection = next;
      loadError = null;
    } catch (err) {
      loadError = err instanceof ApiError ? err.message : 'Der Status der App konnte nicht geladen werden.';
    }
  }

  /** The app restarts with another connection: wait until it answers with it, then load it anew. */
  async function awaitRestart(to: Connection): Promise<void> {
    restarting = to;
    const until = Date.now() + RESTART_GIVE_UP_MS;
    while (Date.now() < until) {
      await new Promise((resolve) => window.setTimeout(resolve, RESTART_POLL_MS));
      try {
        const next = await getConnection();
        if (next.active === to && !next.restarting) {
          // The start page follows from the new state: the overview, or the setup to pair a gateway.
          window.location.hash = '';
          window.location.reload();
          return;
        }
      } catch {
        // The backend is restarting; ask again.
      }
    }
    restarting = null;
    loadError = 'Die App ist nach dem Umstellen nicht wieder erreichbar. Bitte die App in Home Assistant neu starten.';
  }

  /** Setup is done: a connection is chosen and the heat pump is set up for it. */
  const ready = $derived(
    connection !== null &&
      connection.connection !== null &&
      (connection.active === 'myvaillant'
        ? connection.myvaillant.zones.length > 0 || connection.myvaillant.hot_water !== null
        : pairing?.paired != null),
  );

  // Start page: the setup wizard until setup is done, the overview otherwise.
  // Switching the route keeps the wizard open after pairing until the user leaves it.
  $effect(() => {
    if (connection !== null && (connection.active === 'myvaillant' || pairing !== null) && route === '') {
      navigate(ready ? 'uebersicht' : 'einrichtung');
    }
  });

  const linkState = $derived(live?.connection ?? connection?.state ?? null);
  const problem = $derived(linkState ? BRIDGE_PROBLEMS[linkState] : undefined);

  onMount(() => {
    let timer: number | undefined;
    const poll = async (): Promise<void> => {
      await refreshStatus();
      const connected = connection?.state === 'connected' && route !== 'einrichtung';
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
      <p class="secondary">{CONNECTION_SUBTITLES[connection?.active ?? 'eebus']}</p>
    </div>
    {#if route === 'uebersicht' && live}
      <div class="header-right">
        {#if ready}
          <ModeSwitch mode={live.mode} disabled={modeBusy} onRequest={requestMode} />
        {/if}
        <HeaderStatus {live} />
      </div>
    {:else if linkState}
      <StatusPill connection={linkState} />
    {/if}
  </header>

  {#if ready && route !== 'einrichtung'}
    <nav aria-label="Seiten">
      <a href="#/uebersicht" aria-current={route === 'uebersicht' ? 'page' : undefined}>Übersicht</a>
      <a href="#/protokoll" aria-current={route === 'protokoll' ? 'page' : undefined}>Protokoll</a>
      <a href="#/einstellungen" aria-current={route === 'einstellungen' ? 'page' : undefined}>Einstellungen</a>
    </nav>
  {/if}

  {#if route === 'uebersicht' && live && ready}
    <ModeBanner
      mode={live.mode}
      confirming={confirmingLive}
      busy={modeBusy}
      error={modeError}
      onConfirm={() => void changeMode('live', true)}
      onCancel={() => (confirmingLive = false)}
    />
  {/if}

  {#if restarting}
    <p class="banner info" role="status">
      Die App startet mit der neuen Anbindung neu. Die Seite lädt gleich von selbst neu …
    </p>
  {:else if loadError}
    <p class="banner error" role="alert">{loadError}</p>
  {:else if problem}
    <p class="banner error" role="alert">{problem}</p>
  {/if}

  <main>
    {#if restarting}
      <!-- Nothing to use until the app answers again. -->
    {:else if connection === null}
      {#if !loadError}
        <p class="secondary" role="status">Status wird geladen …</p>
      {/if}
    {:else if route === 'einrichtung'}
      <Setup
        {connection}
        {pairing}
        onConnection={(next) => (connection = next)}
        onPairing={(next) => (pairing = next)}
        onRestart={(to) => void awaitRestart(to)}
        onFinish={() => {
          void refreshStatus();
          navigate('uebersicht');
        }}
      />
    {:else if route === 'einstellungen'}
      <Settings
        {connection}
        status={pairing}
        zones={live?.heat_pump?.zones ?? []}
        homeAssistant={live?.home_assistant ?? null}
        onRepair={() => navigate('einrichtung')}
        onConnection={(next) => (connection = next)}
        onRestart={(to) => void awaitRestart(to)}
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
