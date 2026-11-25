import {
  mutationOptions,
  type QueryClient,
  queryOptions,
  useMutation,
  useQuery,
} from "@tanstack/react-query";
import { z } from "zod";

import { jsonRequest, requestJson } from "@/api/client";
import { AGENT_MODES, DEFAULT_REASONING_EFFORT, REASONING_EFFORTS } from "@/api/conversations";
import { apiUrl } from "@/lib/health";

/** The choices the user made for their runs, which the server applies to every turn (see backend `Preferences`). */
const PreferencesSchema = z.object({
  /** Reference of the chosen assistant, such as `~/name`, null for none. */
  assistant: z.string().nullable(),
  mode: z.enum(AGENT_MODES),
  /** Catalog id of the chosen chat model, null for the server's default. */
  model: z.string().nullable(),
  reasoning_effort: z.enum(REASONING_EFFORTS),
});
export type Preferences = z.infer<typeof PreferencesSchema>;

/** What the server answers for a user who never chose, shown until the preferences load. */
const DEFAULTS: Preferences = {
  assistant: null,
  mode: "interactive",
  model: null,
  reasoning_effort: DEFAULT_REASONING_EFFORT,
};

const url = apiUrl("/preferences");

const all = ["preferences"] as const;

export const preferenceQueries = {
  get: () =>
    queryOptions({
      queryKey: all,
      queryFn: () => requestJson(url, "fetchPreferences", PreferencesSchema),
    }),
};

/** The user's preferences as cached, the defaults until they load, for code outside a component. */
export function cachedPreferences(client: QueryClient): Preferences {
  return client.getQueryData(preferenceQueries.get().queryKey) ?? DEFAULTS;
}

/** The user's preferences, the defaults until they load. */
export function usePreferences(): Preferences {
  return useQuery(preferenceQueries.get()).data ?? DEFAULTS;
}

export const preferenceMutations = {
  /** Change some preferences, shown at once, then what the server stored, refetched after a failure. */
  set: () =>
    mutationOptions({
      mutationKey: [...all, "set"],
      mutationFn: async (changes: Partial<Preferences>, { client }) => {
        const stored = await requestJson(
          url,
          "updatePreferences",
          PreferencesSchema,
          jsonRequest("PATCH", changes),
        );
        client.setQueryData(all, stored);

        return stored;
      },
      onMutate: (changes, { client }) => {
        client.setQueryData(all, (current) => ({ ...(current ?? DEFAULTS), ...changes }));
      },
      onSettled: (_stored, error, _changes, _result, { client }) =>
        error ? client.invalidateQueries({ queryKey: all }) : undefined,
    }),
};

/** Change some of the user's preferences, see `preferenceMutations.set`. */
export function useSetPreferences(): (changes: Partial<Preferences>) => void {
  return useMutation(preferenceMutations.set()).mutate;
}
