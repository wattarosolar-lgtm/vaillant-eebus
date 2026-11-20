// Settings cards register whether they hold changes the user has not saved yet;
// leaving the page (a link in the app or the browser) asks first.

const sources = new Set<() => boolean>();

export const UNSAVED_QUESTION = 'Es gibt Änderungen, die noch nicht gespeichert sind. Seite trotzdem verlassen?';

/** Registers a check for unsaved changes; returns the function that removes it. */
export function guardUnsaved(isDirty: () => boolean): () => void {
  sources.add(isDirty);
  return () => {
    sources.delete(isDirty);
  };
}

export function hasUnsaved(): boolean {
  return [...sources].some((isDirty) => isDirty());
}
