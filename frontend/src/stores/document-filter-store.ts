import { create } from "zustand";

import type { DocumentSelection, FilterEntry } from "@/api/filters";
import type { WorkspaceChanged } from "@/api/jobs";
import { followEach, followPath } from "@/lib/utils";
import { onWorkspaceChanged } from "@/stores/jobs-store";

/**
 * Per-conversation document selection for the chat agent. Entries name
 * canonical paths (`~/docs/report.md`, `@team/notes`) and whether each is a
 * directory, a bare scope (`~`, `@team`) being a whole workspace. The two halves
 * are not symmetric: `included` points the agent at the documents a question
 * is about and restricts nothing, while `excluded` is the only half its tools
 * enforce. Every message the user sends carries the selection in its metadata
 * (`selectionMetadata`), which the server stores with it, and the selection survives
 * between messages of the same conversation; starting or switching
 * conversations clears it.
 */
interface DocumentFilterState {
  included: FilterEntry[];
  excluded: FilterEntry[];
  /** Add to the relevant list, or remove when already there (toggle). */
  toggleInclude: (entry: FilterEntry) => void;
  /** Add to the hidden list, or remove when already there (toggle). */
  toggleExclude: (entry: FilterEntry) => void;
  /** Drop the entry from both lists (badge dismissal). */
  remove: (path: string) => void;
  /**
   * Follow what one workspace change moved and deleted: drop entries at or
   * below a deleted path, then carry those at or below a moved source over to
   * its destination, keeping the state when nothing changed.
   */
  follow: (changed: WorkspaceChanged) => void;
  /** Reset both lists when leaving the current conversation. */
  clear: () => void;
}

function has(entries: FilterEntry[], path: string): boolean {
  return entries.some((entry) => entry.path === path);
}

function without(entries: FilterEntry[], path: string): FilterEntry[] {
  return entries.filter((entry) => entry.path !== path);
}

export const useDocumentFilterStore = create<DocumentFilterState>((set) => ({
  included: [],
  excluded: [],
  toggleInclude: (entry) =>
    set((s) =>
      has(s.included, entry.path)
        ? { included: without(s.included, entry.path) }
        : { included: [...s.included, entry], excluded: without(s.excluded, entry.path) },
    ),
  toggleExclude: (entry) =>
    set((s) =>
      has(s.excluded, entry.path)
        ? { excluded: without(s.excluded, entry.path) }
        : { excluded: [...s.excluded, entry], included: without(s.included, entry.path) },
    ),
  remove: (path) =>
    set((s) => ({ included: without(s.included, path), excluded: without(s.excluded, path) })),
  follow: (changed) => {
    if (changed.moves.length === 0 && changed.deletes.length === 0) return;

    const follow = (entry: FilterEntry): FilterEntry | null => {
      const path = followPath(entry.path, changed);

      if (path === null) return null;

      return path === entry.path ? entry : { ...entry, path };
    };

    set((s) => {
      const included = followEach(s.included, follow);
      const excluded = followEach(s.excluded, follow);

      return included === s.included && excluded === s.excluded ? s : { included, excluded };
    });
  },
  clear: () => set({ included: [], excluded: [] }),
}));

// Every workspace change says what it moved and deleted, whoever made it (this
// tab, another one, the agent, an MCP client), and an entry left at a stale
// path would silently stop hiding its document from the agent.
onWorkspaceChanged((changed) => useDocumentFilterStore.getState().follow(changed));

/** The metadata of a message the user sends now, naming the documents it is about. */
export function selectionMetadata(): { documents: DocumentSelection } {
  const { included, excluded } = useDocumentFilterStore.getState();

  return { documents: { included, excluded } };
}
