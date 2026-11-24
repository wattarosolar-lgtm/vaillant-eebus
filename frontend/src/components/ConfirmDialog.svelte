<script lang="ts">
  import { confirmation } from '../lib/confirm.svelte';

  let dialog = $state<HTMLDialogElement | null>(null);
  // Focus on the harmless choice, so an accidental Enter does not start anything.
  let cancelButton = $state<HTMLButtonElement | null>(null);

  const question = $derived(confirmation.current);

  $effect(() => {
    if (dialog === null) {
      return;
    }
    if (question !== null && !dialog.open) {
      dialog.showModal();
      cancelButton?.focus();
    } else if (question === null && dialog.open) {
      dialog.close();
    }
  });
</script>

<!-- Escape fires "cancel": that is a "no". -->
<dialog
  bind:this={dialog}
  aria-labelledby="confirm-question"
  oncancel={(event) => {
    event.preventDefault();
    confirmation.answer(false);
  }}
>
  {#if question}
    <p id="confirm-question">{question.text}</p>
    <div class="actions">
      <button type="button" class="button" bind:this={cancelButton} onclick={() => confirmation.answer(false)}>
        Abbrechen
      </button>
      <button type="button" class="button primary" onclick={() => confirmation.answer(true)}>{question.yes}</button>
    </div>
  {/if}
</dialog>

<style>
  dialog {
    width: min(520px, calc(100vw - 32px));
    padding: 20px;
    border: 1px solid var(--border);
    border-radius: var(--radius-card);
    background: var(--surface);
    color: var(--text);
  }

  dialog::backdrop {
    background: rgb(26 30 28 / 40%);
  }

  p {
    margin: 0 0 16px;
    font-size: 15px;
    line-height: 1.45;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
  }
</style>
