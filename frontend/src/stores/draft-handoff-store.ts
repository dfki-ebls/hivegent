import type { ChatMessage } from "@/lib/chat/chat-utils";
import { create } from "zustand";

/**
 * Hands the first turn of a freshly minted conversation from the draft chat
 * to the `/conversations/$id` route it navigates to. Seeding the messages
 * locally lets the destination skip the history fetch, so the just-streamed
 * turn never flashes through a loading state on the remount.
 */
interface DraftHandoffState {
  messages: Record<string, ChatMessage[]>;
  stash: (id: string, messages: ChatMessage[]) => void;
  take: (id: string) => ChatMessage[] | undefined;
}

export const useDraftHandoffStore = create<DraftHandoffState>((set, get) => ({
  messages: {},
  stash: (id, messages) => set((state) => ({ messages: { ...state.messages, [id]: messages } })),
  take: (id) => {
    const taken = get().messages[id];
    if (taken) {
      set((state) => {
        const { [id]: _removed, ...rest } = state.messages;
        return { messages: rest };
      });
    }
    return taken;
  },
}));
