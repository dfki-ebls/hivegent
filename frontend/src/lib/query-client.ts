import { matchQuery, MutationCache, QueryClient } from "@tanstack/react-query";

import { toast } from "@/components/ui/toast";
import { HttpError } from "@/lib/api";
import { errorMessage } from "@/lib/utils";

/** The first segment of every query and mutation key, so a stray key fails to type-check. */
type KeyScope =
  | "admin"
  | "conversations"
  | "debug"
  | "jobs"
  | "pipelines"
  | "settings"
  | "tools"
  | "workspace";

type AppKey = readonly [KeyScope, ...ReadonlyArray<unknown>];

declare module "@tanstack/react-query" {
  interface Register {
    queryKey: AppKey;
    mutationKey: AppKey;
    mutationMeta: {
      /**
       * The queries a success refetches before the mutation settles, `"all"`
       * for every one, so a component never waits on stale data.
       */
      invalidates?: ReadonlyArray<AppKey> | "all";
      /** The component renders the failure, so no toast is raised. */
      inlineError?: boolean;
    };
  }
}

/** Retry server and network failures, never a request the backend rejected as invalid. */
const retry = (failures: number, error: Error) =>
  failures < 3 && !(error instanceof HttpError && error.status < 500);

/**
 * The cache of server state.  A mutation declares what it changes through
 * `meta.invalidates` instead of refetching by hand, and a failed one surfaces
 * as a toast unless it renders or handles the error itself, so most callers
 * only handle success.
 */
export function createQueryClient(): QueryClient {
  const client = new QueryClient({
    defaultOptions: { queries: { retry } },
    mutationCache: new MutationCache({
      onSuccess: (_data, _variables, _onMutateResult, { meta }) => {
        const invalidates = meta?.invalidates;

        if (invalidates === undefined) return;

        return client.invalidateQueries(
          invalidates === "all"
            ? undefined
            : {
                predicate: (query) =>
                  invalidates.some((queryKey) => matchQuery({ queryKey }, query)),
              },
        );
      },
      // A mutation with its own `onError` reports the failure itself.
      onError: (error, _variables, _onMutateResult, { meta, options }) => {
        if (!meta?.inlineError && !options.onError) {
          toast.add({ type: "error", title: errorMessage(error) });
        }
      },
    }),
  });

  return client;
}
