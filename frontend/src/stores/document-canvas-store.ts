/**
 * Store for the document canvas tab.
 *
 * The active tab is persisted to sessionStorage so a reload keeps the user on
 * the view they were last looking at. Navigation, by contrast, is app-driven:
 * opening a conversation surfaces its Context, while a fresh draft starts on
 * Documents. `openChat` reconciles the two — a reload restores the remembered
 * tab, a real in-session navigation surfaces the destination's primary view.
 *
 * Adding a future view (e.g. a graph or database panel) only touches the schema
 * and the canvas registry; the surfacing rules below stay as they are until
 * such a view earns its own auto-surface trigger.
 */

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import { type DocumentCanvasTab, DocumentCanvasTabSchema } from "../lib/types";

interface DocumentCanvasState {
  activeTab: DocumentCanvasTab;
  // Both fields are in-memory only. `lastChatId` is null right after a fresh
  // load, which is how a reload is told apart from an in-session navigation;
  // `restored` records whether sessionStorage actually held a tab worth keeping
  // on that first load (vs. a cold start that should follow the route default).
  lastChatId: string | null;
  restored: boolean;
  setActiveTab: (tab: DocumentCanvasTab) => void;
  openChat: (id: string, draft: boolean) => void;
}

export const useDocumentCanvasStore = create<DocumentCanvasState>()(
  persist(
    (set) => ({
      activeTab: "documents",
      lastChatId: null,
      restored: false,
      setActiveTab: (activeTab) => set({ activeTab }),
      openChat: (id, draft) =>
        set((state) => {
          if (state.lastChatId === id) return {};
          const keepRemembered = state.lastChatId === null && state.restored;
          return {
            lastChatId: id,
            activeTab: keepRemembered ? state.activeTab : draft ? "documents" : "context",
          };
        }),
    }),
    {
      name: "hivegent-document-canvas",
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({ activeTab: state.activeTab }),
      merge: (persisted, current) => {
        const stored = DocumentCanvasTabSchema.safeParse(
          (persisted as { activeTab?: unknown } | undefined)?.activeTab,
        ).data;
        return { ...current, activeTab: stored ?? current.activeTab, restored: stored != null };
      },
    },
  ),
);
