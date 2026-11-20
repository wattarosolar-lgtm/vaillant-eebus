<script lang="ts">
  import { WriteFeedback } from '../lib/feedback.svelte';
  import type { OperatingMode, WriteResult } from '../lib/types';

  let {
    mode,
    ending,
    send,
  }: {
    mode: OperatingMode;
    /** Live: the end was sent, myVAILLANT has not reported the time program yet. */
    ending: boolean;
    /** Ends the override; shown is the operating mode on screen when the user clicked. */
    send: (shown: OperatingMode) => Promise<WriteResult>;
  } = $props();

  const feedback = new WriteFeedback<boolean>();
  $effect(() => feedback.follow(mode));
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
</style>
