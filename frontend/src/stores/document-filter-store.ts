import { create } from "zustand";

import type { PathChanges } from "@/lib/types";
import { followEach, followPath } from "@/lib/utils";
import { onCommitted } from "@/stores/jobs-store";

/**
 * Per-conversation document selection for the chat agent. Entries are
 * canonical paths (`~/docs/report.md`, `@team/notes/`): a trailing slash marks
 * a directory, a bare scope (`~`, `@team`) a whole workspace. The two halves
 * are not symmetric: `included` points the agent at the documents a question
 * is about and restricts nothing, while `excluded` is the only half its tools
 * enforce. The selection rides along with every request and survives between
 * messages of the same conversation; starting or switching conversations
 * clears it.
 */
interface DocumentFilterState {
  included: string[];
  excluded: string[];
  /** Add to the relevant list, or remove when already there (toggle). */
  toggleInclude: (path: string) => void;
  /** Add to the hidden list, or remove when already there (toggle). */
  toggleExclude: (path: string) => void;
  /** Drop the entry from both lists (badge dismissal). */
  remove: (path: string) => void;
  /**
   * Follow what one commit moved and deleted: drop entries at or below a
   * deleted path, then carry those at or below a moved source over to its
   * destination, keeping the state when nothing changed.
   */
  follow: (changes: PathChanges) => void;
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
  follow: (changes) => {
    if (changes.moves.length === 0 && changes.deletes.length === 0) return;

    const follow = (path: string) => followPath(path, changes);

    set((s) => {
      const included = followEach(s.included, follow);
      const excluded = followEach(s.excluded, follow);

      return included === s.included && excluded === s.excluded ? s : { included, excluded };
    });
  },
  clear: () => set({ included: [], excluded: [] }),
}));

// Every commit of the changeset gateway says what it moved and deleted,
// whoever made it (this tab, another one, the agent, an MCP client), and an
// entry left at a stale path would silently stop hiding its document from the agent.
onCommitted((changes) => useDocumentFilterStore.getState().follow(changes));
