<script lang="ts">
  import { WriteFeedback } from '../lib/feedback.svelte';
  import type { OperatingMode, WriteResult } from '../lib/types';

  let {
    mode,
    ending,
    send,
    extend = null,
  }: {
    mode: OperatingMode;
    /** Live: the end was sent, myVAILLANT has not reported the time program yet. */
    ending: boolean;
    /** Ends the override; shown is the operating mode on screen when the user clicked. */
    send: (shown: OperatingMode) => Promise<WriteResult>;
    /** Sets the override again with the same value for a new duration (W6); null without a known value. */
    extend?: ((hours: number, shown: OperatingMode) => Promise<WriteResult>) | null;
  } = $props();

  const HOURS = [1, 2, 3, 4, 6, 8, 12];
  let hours = $state(3);
  const feedback = new WriteFeedback<boolean>();
  const extension = new WriteFeedback<number>();
  $effect(() => feedback.follow(mode));
  $effect(() => extension.follow(mode));
</script>

<div class="override-end">
  {#if ending}
    <span class="hint" role="status">Übersteuerung wird beendet; myVAILLANT meldet das Ende gleich.</span>
  {:else}
    <button
      type="button"
      class="button small"
      disabled={feedback.busy}
      onclick={() => {
        const onScreen = mode;
        void feedback.submit(true, () => send(onScreen));
      }}
    >
      {feedback.busy ? 'Wird gesendet …' : 'Übersteuerung beenden'}
    </button>
    {#if extend}
      <span class="extend">
        <label class="sr-only" for="override-hours">Neue Dauer</label>
        <select id="override-hours" bind:value={hours} disabled={extension.busy}>
          {#each HOURS as item (item)}
            <option value={item}>{item} h</option>
          {/each}
        </select>
        <button
          type="button"
          class="button small"
          disabled={extension.busy}
          title="Gleiches Soll, neue Dauer ab jetzt; das Energiemanagement lässt die Zone dann in Ruhe."
          onclick={() => {
            const onScreen = mode;
            const chosen = hours;
            void extension.submit(chosen, () => extend(chosen, onScreen));
          }}
        >
          {extension.busy ? 'Wird gesendet …' : 'Verlängern'}
        </button>
      </span>
    {/if}
    {#if extension.error}
      <span class="error" role="alert">{extension.error}</span>
    {:else if extension.preview !== null}
      <span class="hint" role="status">Demo: nicht gesendet</span>
    {/if}
    {#if feedback.error}
      <span class="error" role="alert">{feedback.error}</span>
    {:else if feedback.preview !== null}
      <span class="hint" role="status">Demo: nicht gesendet</span>
    {/if}
  {/if}
</div>

<style>
  .override-end {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: center;
    gap: 6px 12px;
  }

  .error {
    color: var(--error-text);
    font-size: 13px;
  }

  .extend {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
</style>
