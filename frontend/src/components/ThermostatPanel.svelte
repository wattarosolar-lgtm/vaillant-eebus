<script lang="ts">
  import { ApiError, roomTest, setRoomStage } from '../lib/api';
  import { formatNumber, maybe, moment, signed } from '../lib/format';
  import { ROOM_CONTROL_LABELS, ROOM_STAGES } from '../lib/texts';
  import type { OperatingMode, RoomControlRoom, RoomsPage, RoomStage } from '../lib/types';
  import StatusPill from './StatusPill.svelte';

  let {
    room,
    appMode,
    probeHere,
    testStart,
    onChange,
  }: {
    room: RoomControlRoom;
    appMode: OperatingMode;
    /** The probe pair hangs at this room's radiator. */
    probeHere: boolean;
    testStart: string;
    onChange: (page: RoomsPage) => void;
  } = $props();

  let busy = $state(false);
  let error = $state<string | null>(null);

  const testActive = $derived(room.test !== null && (room.test.state === 'armed' || room.test.state === 'running'));
  const preview = $derived(room.effective === 'preview' || (room.effective === 'control' && appMode === 'demo'));
  const commands = $derived(preview ? room.preview_commands_today : room.commands_today);

  async function changeStage(stage: RoomStage): Promise<void> {
    let confirm = false;
    if (stage === 'control') {
      confirm = window.confirm(
        `${room.name} auf „Steuern“: Die App stellt ab jetzt das Thermostat dieses Raums. Fortfahren?`,
      );
      if (!confirm) {
        return;
      }
    }
    await act(() => setRoomStage(room.room_id, stage, confirm));
  }

  async function arm(): Promise<void> {
    const confirm = window.confirm(
      `Testlauf ${room.name}: In der kommenden Nacht ab ${testStart} Uhr stellt die App das Thermostat Schritt für ` +
        'Schritt höher und wieder tiefer (3–6 Stunden, höchstens 16 Befehle). Der Raum kann dabei bis zu 1 K wärmer ' +
        'werden. Vormerken?',
    );
    if (confirm) {
      await act(() => roomTest(room.room_id, 'arm', true));
    }
  }

  async function act(call: () => Promise<RoomsPage>): Promise<void> {
    busy = true;
    error = null;
    try {
      onChange(await call());
    } catch (err) {
      error = err instanceof ApiError ? err.message : 'Das hat nicht geklappt.';
    } finally {
      busy = false;
    }
  }
</script>

<div class="panel" aria-label={`Raumregelung ${room.name}`}>
  <div class="head">
    <span class="title">Thermostat{room.model_name && room.model !== 'other' ? ` · ${room.model_name}` : ''}</span>
    <StatusPill
      label={`Wirksam: ${ROOM_CONTROL_LABELS[room.effective]}`}
      tone={room.effective === 'control' ? 'ok' : 'neutral'}
    />
  </div>

  <div class="segments" role="group" aria-label={`Stufe ${room.name}`}>
    {#each ROOM_STAGES as stage (stage)}
      {@const blocked = stage === 'control' && room.stage !== 'control' && !room.can_control}
      <button
        type="button"
        class:active={room.stage === stage}
        aria-pressed={room.stage === stage}
        disabled={busy || blocked}
        title={blocked ? (room.control_blocked ?? undefined) : undefined}
        onclick={() => void changeStage(stage)}>{ROOM_CONTROL_LABELS[stage]}</button
      >
    {/each}
  </div>
  {#if room.stage !== 'control' && room.control_blocked}
    <p class="hint">Steuern: {room.control_blocked}</p>
  {/if}

  <dl class="grid-facts">
    <div>
      <dt>Thermostat-Soll</dt>
      <dd>{maybe(room.trv_setpoint_c, 1, '°C')}</dd>
    </div>
    <div>
      <dt>{preview ? 'Würde stellen' : 'Ziel'}</dt>
      <dd>{maybe(room.target_c, 1, '°C')}</dd>
    </div>
    <div>
      <dt>Am Thermostat gemessen</dt>
      <dd>{maybe(room.trv_c, 1, '°C')}</dd>
    </div>
    <div>
      <dt>{room.at_thermostat ? 'Fester Ausgleich' : 'Korrektur'}</dt>
      <dd>{signed(room.at_thermostat ? room.offset_k : room.correction_k)} K</dd>
    </div>
    {#if room.mode_name}
      <div class="wide">
        <dt>Zustand</dt>
        <dd>
          {room.mode_name}{room.reason_name ? ` · ${room.reason_name}` : ''}
          {#if room.note}<span class="hint">({room.note})</span>{/if}
        </dd>
      </div>
    {/if}
    <div>
      <dt>Befehle heute{preview ? ' (würde)' : ''}</dt>
      <dd>{commands} von {room.commands_limit}{room.test_commands_today > 0 ? ` · Testlauf ${room.test_commands_today}` : ''}</dd>
    </div>
    <div>
      <dt>Ventilbewegungen heute</dt>
      <dd>{room.movements_today}</dd>
    </div>
  </dl>
  {#if room.donor}
    <p class="hint">Wird gerade gedrosselt, damit ein kalter Raum mehr Wasser bekommt.</p>
  {/if}
  {#if room.cold}
    <p class="hint">Unterversorgt: andere Räume geben ab (Umverteilen).</p>
  {/if}
  {#if room.paused_until}
    <p class="warn">Pausiert bis {moment(room.paused_until)} Uhr: das Thermostat hat zwei Befehle nicht bestätigt.</p>
  {/if}
  {#if room.warnings.length > 0}
    <ul class="warnings" aria-label={`Hinweise zum Thermostat ${room.name}`}>
      {#each room.warnings as warning (warning)}
        <li>{warning}</li>
      {/each}
    </ul>
  {/if}
  {#if room.recommendation}
    <p class="hint">Voreinstellung: {room.recommendation}</p>
  {/if}

  <div class="test">
    <span class="title">Testlauf</span>
    {#if room.test}
      <p>
        <StatusPill label={room.test.state_name} tone={room.test.state === 'failed' ? 'warn' : 'neutral'} />
        {room.test.message}
        {#if room.test.state === 'running'}
          <span class="hint">· {room.test.steps} Stufen gemessen, Soll {maybe(room.test.setpoint_c, 1, '°C')}</span>
        {/if}
        {#if room.test.state === 'armed' && room.test.nights > 0}
          <span class="hint">· {room.test.nights} von 3 Nächten ohne Start</span>
        {/if}
      </p>
    {/if}
    {#if testActive}
      <div>
        <button type="button" class="button small" disabled={busy} onclick={() => void act(() => roomTest(room.room_id, 'cancel', false))}
          >Testlauf abbrechen</button
        >
      </div>
    {:else if !probeHere}
      <p class="hint">Für einen Testlauf das Fühlerpaar an diesen Heizkörper hängen und oben unter „Fühlerpaar“ zuordnen.</p>
    {:else if appMode !== 'live'}
      <p class="hint">Ein Testlauf stellt das Thermostat und geht nur im Live-Betrieb.</p>
    {:else}
      <div><button type="button" class="button small" disabled={busy} onclick={() => void arm()}>Testlauf für heute Nacht vormerken</button></div>
    {/if}
  </div>

  <details>
    <summary>
      Ventilverhalten {room.curve.learned ? 'gelernt' : 'noch nicht gelernt'}
      ({room.curve.complete_runs} von {room.curve.runs} Testläufen vollständig)
    </summary>
    {#if room.curve.steps.length === 0}
      <p class="hint">Noch kein Testlauf. Empfohlen sind 2–3 Läufe an verschiedenen Tagen.</p>
    {:else}
      <p class="hint">
        Öffnet ab {room.curve.opening_k === null ? '–' : `${signed(room.curve.opening_k)} K`}, ganz offen ab
        {room.curve.full_k === null ? '–' : `${signed(room.curve.full_k)} K`}, zu ab
        {room.curve.closing_k === null ? '–' : `${signed(room.curve.closing_k)} K`} (Sollwert über der Temperatur am
        Thermostat); reagiert nach etwa {maybe(room.curve.reaction_min, 0, 'min')}.
        {#if room.curve.levels.length > 0}Erreichte Stufen: {room.curve.levels.map((level) => `${formatNumber(level, 0)} %`).join(', ')}.{/if}
        {#if room.curve.last_at}Letzter Lauf {moment(room.curve.last_at)} Uhr ({room.curve.last_ending}).{/if}
      </p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Soll</th>
              <th scope="col">Abstand</th>
              <th scope="col">Ventil</th>
              <th scope="col">VL / RL</th>
              <th scope="col">Wärme</th>
              <th scope="col">min</th>
            </tr>
          </thead>
          <tbody>
            {#each room.curve.steps as step, index (index)}
              <tr>
                <td>{formatNumber(step.setpoint_c, 1)} {step.rising ? '↑' : '↓'}</td>
                <td>{step.offset_k === null ? '–' : signed(step.offset_k)}</td>
                <td>{maybe(step.valve, 0, '%')}</td>
                <td>{maybe(step.flow_c, 1)} / {maybe(step.return_c, 1)}</td>
                <td>{maybe(step.heat_w, 0, 'W')}</td>
                <td>{formatNumber(step.minutes, 0)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </details>
  {#if error}
    <p class="error" role="alert">{error}</p>
  {/if}
</div>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--divider);
    min-width: 0;
  }

  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
  }

  .title {
    font-size: 15px;
    font-weight: 600;
  }

  .segments {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .segments > button {
    padding: 0 4px;
  }

  dl.grid-facts {
    margin: 0;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px 20px;
  }

  dl.grid-facts > div {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  dl.grid-facts > div.wide {
    grid-column: 1 / -1;
  }

  dl.grid-facts dt {
    font-size: 13px;
    color: var(--text-secondary);
  }

  dl.grid-facts dd {
    margin: 0;
    text-align: left;
    font-size: 15px;
    font-weight: 500;
  }

  .test {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .test p {
    margin: 0;
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    font-size: 14px;
  }

  .warnings {
    margin: 0;
    padding-left: 20px;
    color: var(--warn-text);
    font-size: 14px;
  }

  .warn {
    color: var(--warn-text);
    font-size: 14px;
  }

  details summary {
    cursor: pointer;
    min-height: 32px;
    font-size: 14px;
    color: var(--text-secondary);
  }

  .table-wrap {
    overflow-x: auto;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    margin: 8px 0;
  }

  th,
  td {
    padding: 4px;
    text-align: left;
    border-bottom: 1px solid var(--divider);
    white-space: nowrap;
  }

  th {
    font-weight: 500;
  }

  .error {
    color: var(--error-text);
    font-size: 14px;
  }

  .button.small {
    min-height: 44px;
    padding: 0 14px;
    font-size: 14px;
  }

  @media (max-width: 420px) {
    dl.grid-facts {
      grid-template-columns: 1fr;
    }
  }
</style>
