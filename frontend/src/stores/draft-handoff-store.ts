import type { ChatMessage } from "@/lib/chat/chat-utils";
import { create } from "zustand";

/**
 * The draft turn a freshly minted conversation inherits on the remount.
 *
 * `error` is the run error as the SDK reported it live, kept apart from the
 * copy `recordChatError` writes into the messages: the metadata renders the
 * banner, while this says the failure belongs to this session rather than to
 * loaded history — the one thing auto-compaction may act on.
 */
export interface DraftHandoff {
  messages: ChatMessage[];
  error?: string;
}

/**
 * Hands the first turn of a freshly minted conversation from the draft chat
 * to the `/conversations/$id` route it navigates to. Seeding the messages
 * locally lets the destination skip the history fetch, so the just-streamed
 * turn never flashes through a loading state on the remount.
 */
interface DraftHandoffState {
  handoffs: Record<string, DraftHandoff>;
  stash: (id: string, handoff: DraftHandoff) => void;
  take: (id: string) => DraftHandoff | undefined;
}

export const useDraftHandoffStore = create<DraftHandoffState>((set, get) => ({
  handoffs: {},
  stash: (id, handoff) => set((state) => ({ handoffs: { ...state.handoffs, [id]: handoff } })),
  take: (id) => {
    const { [id]: taken, ...handoffs } = get().handoffs;
    if (taken) set({ handoffs });
    return taken;
  },
}));
