import type { ChatStatus } from "ai";
import { useCallback, useState } from "react";
import { isChatBusy } from "@/lib/chat/chat-utils";

/** The message the user edits, dropped once a turn runs, while a failed turn stays editable. */
export function useMessageEditing(status: ChatStatus) {
  const [editingId, setEditingId] = useState<string | null>(null);

  if (isChatBusy(status) && editingId !== null) {
    setEditingId(null);
  }

  const setEditing = useCallback((id: string) => setEditingId(id), []);
  const clear = useCallback(() => setEditingId(null), []);

  return { editingId, setEditing, clear };
}
