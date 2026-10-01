import type { ChatMessage } from "@/lib/chat/chat-utils";
import { useEffect, useState } from "react";
import { getConversation, getConversationMessages } from "@/lib/api";
import { useDraftHandoffStore } from "@/stores/draft-handoff-store";

interface LoadedHistory {
  id: string;
  compactedFrom: string | null;
}

export function useConversationHistory(
  id: string,
  setMessages: (messages: ChatMessage[]) => void,
  draft = false,
) {
  // Tagged with the conversation it was loaded for, so switching conversations
  // reads as loading again without resetting state inside the effect.
  const [loaded, setLoaded] = useState<LoadedHistory | null>(null);
  const takeHandoff = useDraftHandoffStore((state) => state.take);

  useEffect(() => {
    // A draft chat owns its messages in memory — there is no server history
    // to load, and loading the id would 404 since it isn't a row yet.
    if (draft) return;
    let cancelled = false;
    let compactedFrom: string | null = null;
    void (async () => {
      try {
        // Messages handed off from the draft we just navigated from: seed them
        // directly so the freshly streamed turn doesn't flash a loading state.
        const seeded = takeHandoff(id);
        if (seeded) {
          setMessages(seeded);
          return;
        }
        // Fetch messages first and skip the summary call (only needed for
        // the compacted-from banner) when there's nothing to show.
        const initialMessages = await getConversationMessages(id);
        if (cancelled || initialMessages.length === 0) return;
        setMessages(initialMessages);
        const conv = await getConversation(id);
        compactedFrom = conv?.compacted_from ?? null;
      } finally {
        if (!cancelled) setLoaded({ id, compactedFrom });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, draft, setMessages, takeHandoff]);

  const current = loaded?.id === id ? loaded : null;

  return { isLoadingHistory: !draft && !current, compactedFrom: current?.compactedFrom ?? null };
}
