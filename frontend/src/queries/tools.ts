import { queryOptions } from "@tanstack/react-query";

import { listToolSchemas, listTools } from "@/lib/api";

const all = ["tools"] as const;

// The built-in tools are fixed per server process, so they never go stale.
export const toolQueries = {
  /** The built-in tools a user can switch off. */
  list: () => queryOptions({ queryKey: [...all, "list"], queryFn: listTools, staleTime: "static" }),
  /** The built-in tools with their parameter schemas, for the admin console. */
  schemas: () =>
    queryOptions({ queryKey: [...all, "schemas"], queryFn: listToolSchemas, staleTime: "static" }),
};
