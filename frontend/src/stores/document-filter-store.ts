import { create } from "zustand";

/**
 * Per-conversation include/exclude filter restricting which workspace
 * documents the chat agent may access. Entries are canonical paths
 * (`~/docs/report.md`, `@team/notes/`): a trailing slash marks a directory,
 * a bare scope (`~`, `@team`) a whole workspace. The filter rides along with
 * every request and survives between messages of the same conversation;
 * starting or switching conversations clears it.
 */
interface DocumentFilterState {
  included: string[];
  excluded: string[];
  /** Add to the include list, or remove when already included (toggle). */
  toggleInclude: (path: string) => void;
  /** Add to the exclude list, or remove when already excluded (toggle). */
  toggleExclude: (path: string) => void;
  /** Drop the entry from both lists (badge dismissal). */
  remove: (path: string) => void;
  /** Reset both lists when leaving the current conversation. */
  clear: () => void;
}

function without(paths: string[], path: string): string[] {
  return paths.filter((p) => p !== path);
}

export const useDocumentFilterStore = create<DocumentFilterState>((set) => ({
  included: [],
  excluded: [],
  toggleInclude: (path) =>
    set((s) =>
      s.included.includes(path)
        ? { included: without(s.included, path) }
        : { included: [...s.included, path], excluded: without(s.excluded, path) },
    ),
  toggleExclude: (path) =>
    set((s) =>
      s.excluded.includes(path)
        ? { excluded: without(s.excluded, path) }
        : { excluded: [...s.excluded, path], included: without(s.included, path) },
    ),
  remove: (path) =>
    set((s) => ({ included: without(s.included, path), excluded: without(s.excluded, path) })),
  clear: () => set({ included: [], excluded: [] }),
}));
