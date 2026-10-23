import { queryOptions } from "@tanstack/react-query";

import { getDebugConversation } from "@/lib/api";

const all = ["debug"] as const;

export const debugQueries = {
  /** Any user's conversation summary, confirming the ID the chat debugger opens. */
  conversation: (id: string) =>
    queryOptions({
      queryKey: [...all, "conversation", id],
      queryFn: () => getDebugConversation(id),
      retry: false,
    }),
};
