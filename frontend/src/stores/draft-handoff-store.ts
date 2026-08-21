import type { ChatMessage } from "@/lib/chat/chat-utils";
import { create } from "zustand";

/**
 * Hands the first turn of a freshly minted conversation from the draft chat
 * to the `/conversations/$id` route it navigates to. Seeding the messages
 * locally lets the destination skip the history fetch, so the just-streamed
 * turn never flashes through a loading state on the remount.
 *
 * A run error travels inside the messages' own metadata (`recordChatError`),
 * so the destination renders the same recovery UI as a conversation loaded
 * from history.
 */
interface DraftHandoffState {
  handoffs: Record<string, ChatMessage[]>;
  stash: (id: string, messages: ChatMessage[]) => void;
  take: (id: string) => ChatMessage[] | undefined;
}

export const useDraftHandoffStore = create<DraftHandoffState>((set, get) => ({
  handoffs: {},
  stash: (id, messages) => set((state) => ({ handoffs: { ...state.handoffs, [id]: messages } })),
  take: (id) => {
    const { [id]: taken, ...handoffs } = get().handoffs;
    if (taken) set({ handoffs });
    return taken;
  },
}));
