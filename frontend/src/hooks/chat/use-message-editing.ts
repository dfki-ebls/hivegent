import type { ChatStatus } from "ai";
import { useCallback, useState } from "react";

export function useMessageEditing(status: ChatStatus) {
  const [editingId, setEditingId] = useState<string | null>(null);

  if (status !== "ready" && editingId !== null) {
    setEditingId(null);
  }

  const setEditing = useCallback((id: string) => setEditingId(id), []);
  const clear = useCallback(() => setEditingId(null), []);

  return { editingId, setEditing, clear };
}
