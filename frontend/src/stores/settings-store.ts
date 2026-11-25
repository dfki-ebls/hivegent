/**
 * Zustand store for the settings the browser keeps, which only shape the interface.
 *
 * Persisted state is Zod-validated, so a stale or foreign entry falls back to
 * its default.  What changes how the server runs a turn are the user's
 * preferences, which the server keeps (`@/api/preferences`).
 */

import type { SetStateAction } from "react";
import { z } from "zod";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import { PERSONAL_SCOPE } from "@/api/documents";

/** Exact shape written to localStorage, each field falling back to its default. */
const PersistedSettingsSchema = z.object({
  /** Canonical ids of the expanded document tree folders, workspaces included. */
  expandedItems: z.array(z.string()).catch([PERSONAL_SCOPE]),
});
type PersistedSettings = z.infer<typeof PersistedSettingsSchema>;

const DEFAULTS = PersistedSettingsSchema.parse({});

interface SettingsState extends PersistedSettings {
  setExpandedItems: (update: SetStateAction<string[]>) => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      ...DEFAULTS,

      setExpandedItems: (update) =>
        set((state) => ({
          expandedItems: typeof update === "function" ? update(state.expandedItems) : update,
        })),
    }),
    {
      name: "hivegent-settings",
      storage: createJSONStorage(() => localStorage),
      partialize: (state): PersistedSettings => PersistedSettingsSchema.parse(state),
      merge: (persisted, current) =>
        persisted
          ? { ...current, ...(PersistedSettingsSchema.safeParse(persisted).data ?? DEFAULTS) }
          : current,
    },
  ),
);
