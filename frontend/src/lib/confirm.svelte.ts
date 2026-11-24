// Questions before a step with consequences, in the app's own dialog. The browser's window.confirm is no good
// here: once the user ticked "no more dialogs from this page", or in the Home Assistant app, it answers "no"
// at once without showing anything, and the button seems dead.

export type Question = {
  text: string;
  /** The label of the button that says yes. */
  yes: string;
};

class Confirmation {
  current = $state<Question | null>(null);
  #resolve: ((yes: boolean) => void) | null = null;

  ask(question: Question): Promise<boolean> {
    // A new question replaces one still open: that one counts as "no".
    this.answer(false);
    this.current = question;
    return new Promise((resolve) => {
      this.#resolve = resolve;
    });
  }

  answer(yes: boolean): void {
    const resolve = this.#resolve;
    this.#resolve = null;
    this.current = null;
    resolve?.(yes);
  }
}

export const confirmation = new Confirmation();

/** Asks in the app's dialog; true only when the user said yes. */
export function ask(text: string, yes = 'Fortfahren'): Promise<boolean> {
  return confirmation.ask({ text, yes });
}
