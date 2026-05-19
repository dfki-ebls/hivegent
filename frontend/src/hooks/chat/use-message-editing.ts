import type { ChatStatus } from "ai";
import { useCallback, useEffect, useState } from "react";

export function useMessageEditing(status: ChatStatus) {
  const [editingId, setEditingId] = useState<string | null>(null);

  useEffect(() => {
    if (status !== "ready") {
      setEditingId(null);
    }
  }, [status]);

  const setEditing = useCallback((id: string) => setEditingId(id), []);
  const clear = useCallback(() => setEditingId(null), []);

  return { editingId, setEditing, clear };
}
