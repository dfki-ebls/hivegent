/**
 * Zustand store for the settings that only shape the interface.
 *
 * It lives in memory alone, since the expanded folders name private documents
 * that must not outlive the session in the browser's storage.  What changes how
 * the server runs a turn are the user's preferences, which the server keeps
 * (`@/api/preferences`).
 */

import type { SetStateAction } from "react";
import { create } from "zustand";

import { PERSONAL_SCOPE } from "@/api/documents";

interface SettingsState {
  /** Canonical ids of the expanded document tree folders, workspaces included. */
  expandedItems: string[];
  setExpandedItems: (update: SetStateAction<string[]>) => void;
}

export const useSettingsStore = create<SettingsState>()((set) => ({
  expandedItems: [PERSONAL_SCOPE],

  setExpandedItems: (update) =>
    set((state) => ({
      expandedItems: typeof update === "function" ? update(state.expandedItems) : update,
    })),
}));
