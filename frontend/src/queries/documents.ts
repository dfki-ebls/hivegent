import {
  hashKey,
  mutationOptions,
  type QueryClient,
  queryOptions,
  useMutationState,
} from "@tanstack/react-query";
import { useMemo } from "react";

import {
  applyChanges,
  canonicalPath,
  type ChangeOperation,
  type DeleteKind,
  deleteAssetDescription,
  generateAssetDescription,
  getDirectories,
  getDocumentChunks,
  getDocumentContent,
  getDocumentLineCounts,
  listDocumentAssets,
  rechunkDocuments,
  reconvertDocuments,
  updateAssetDescription,
  writeDocument,
} from "@/lib/api";
import type {
  AssetEntry,
  DirectoryTreeResponse,
  JobView,
  LlmConfig,
  PipelineSpec,
} from "@/lib/types";
import { withDirectory } from "@/lib/utils";
import {
  awaitJobSettled,
  onWorkspaceStale,
  publishWorkspaceChanged,
  useJobsStore,
} from "@/stores/jobs-store";

const all = ["workspace"] as const;

type LineCount = number | null;

/** Line count lookups waiting for the batch request of the current tick. */
let pendingLineCounts = new Map<string, PromiseWithResolvers<LineCount>>();

function flushLineCounts(): void {
  const batch = pendingLineCounts;
  pendingLineCounts = new Map();

  getDocumentLineCounts([...batch.keys()]).then(
    (counts) => {
      for (const [path, { resolve }] of batch) resolve(counts[path] ?? null);
    },
    (error: unknown) => {
      for (const { reject } of batch.values()) reject(error);
    },
  );
}

/** Look *path* up in the batch request of the current tick. */
function fetchLineCount(path: string): Promise<LineCount> {
  if (pendingLineCounts.size === 0) queueMicrotask(flushLineCounts);

  const lookup = pendingLineCounts.get(path) ?? Promise.withResolvers<LineCount>();
  pendingLineCounts.set(path, lookup);

  return lookup.promise;
}

const treeKey = (scope: string) => [...all, "tree", scope] as const;

/** Tree reads that a refresh asked for while they were in flight, by query hash. */
const staleReads = new Set<string>();

export const documentQueries = {
  /**
   * The directory tree of one scope (`~` or `@<group>`).
   *
   * A read walks the entire workspace, so a burst of changes must not start
   * one each.  {@link refreshTrees} never cancels a read in flight, it marks
   * it stale, and the read repeats once for every change that arrived during
   * it.  A read can neither predate the change that asked for it nor overtake
   * another one.
   */
  tree: (scope: string) =>
    queryOptions({
      queryKey: treeKey(scope),
      queryFn: async ({ queryKey, signal }) => {
        const hash = hashKey(queryKey);
        let tree: DirectoryTreeResponse;

        do {
          staleReads.delete(hash);
          tree = await getDirectories(scope, signal);
        } while (staleReads.has(hash) && !signal.aborted);

        return tree;
      },
      staleTime: Infinity,
    }),
  /** The raw content of a workspace document, or of a `/tmp` file of *conversationId*. */
  content: (path: string, conversationId: string | null = null) =>
    queryOptions({
      queryKey: [...all, "content", path, conversationId],
      queryFn: () => getDocumentContent(path, conversationId),
    }),
  /** The indexed chunks and entry metadata of a workspace document. */
  chunks: (path: string) =>
    queryOptions({
      queryKey: [...all, "chunks", path],
      queryFn: () => getDocumentChunks(path),
    }),
  /** The extracted assets of a workspace document. */
  assets: (path: string) =>
    queryOptions({
      queryKey: [...all, "assets", path],
      queryFn: () => listDocumentAssets(path),
    }),
  /**
   * The indexed line count of a document, `null` when the backend has none
   * (`/tmp`, not yet indexed).  The lookups of one tick share a request.
   */
  lineCount: (path: string) =>
    queryOptions({
      queryKey: [...all, "line-count", path],
      queryFn: () => fetchLineCount(path),
      staleTime: Infinity,
    }),
};

/** One change to the description of an extracted asset. */
export type AssetDescriptionChange =
  | { action: "save"; name: string; description: string }
  | { action: "generate"; name: string; llm: LlmConfig }
  | { action: "delete"; name: string };

/**
 * Refetch the trees of *scopes* without cancelling a read in flight, marking
 * it stale instead.  Resolves once the shown trees reflect the change.
 */
function refreshTrees(client: QueryClient, scopes: readonly string[] | "all") {
  const keys = scopes === "all" ? [[...all, "tree"] as const] : scopes.map(treeKey);

  return Promise.all(
    keys.map((queryKey) => {
      for (const query of client.getQueryCache().findAll({ queryKey, fetchStatus: "fetching" })) {
        staleReads.add(query.queryHash);
      }

      return client.invalidateQueries({ queryKey }, { cancelRefetch: false });
    }),
  );
}

/** A change to the tree of `scope`, whose rows at `paths` spin while it runs. */
interface TreeChange {
  scope: string;
  paths: readonly string[];
}

function isTreeChange(variables: unknown): variables is TreeChange {
  return (
    typeof variables === "object" &&
    variables !== null &&
    "scope" in variables &&
    "paths" in variables &&
    Array.isArray(variables.paths)
  );
}

/**
 * A mutation of the tree of its `scope`.  The change publishes what it did,
 * which refreshes every tree it touched, and the mutation stays pending until
 * this scope's read lands, so its rows spin until the tree shows the outcome.
 * A failure refreshes too, since the view may hold an entry the backend no
 * longer knows.
 */
function treeMutation<V extends TreeChange>(
  action: string,
  change: (variables: V, client: QueryClient) => Promise<void>,
) {
  return mutationOptions({
    mutationKey: [...all, "tree", action],
    mutationFn: async (variables: V, { client }) => {
      try {
        await change(variables, client);
      } catch (error) {
        await refreshTrees(client, [variables.scope]);
        throw error;
      }

      await client.query(documentQueries.tree(variables.scope));
    },
  });
}

/** Apply workspace changes as one request and publish what the backend applied. */
async function applyAndPublish(operations: ChangeOperation[]): Promise<void> {
  // This tab's own feed skips the change, and what was sent is not what
  // applied, so the response is what refreshes the scopes and moves kept paths.
  publishWorkspaceChanged(await applyChanges(operations));
}

/** Submit a background job, show it in the tray, and wait for it to settle. */
async function runJob(submit: () => Promise<JobView>): Promise<void> {
  const job = await submit();
  useJobsStore.getState().upsert(job);
  await awaitJobSettled(job.id);
}

/** Rows of *scope* that a pending mutation is changing, until its tree refreshes. */
export function useMutatingPaths(scope: string): ReadonlySet<string> {
  const pending = useMutationState({
    filters: { mutationKey: [...all, "tree"], status: "pending" },
    select: ({ state }) => state.variables,
  });

  return useMemo(
    () =>
      new Set(
        pending.flatMap((variables) =>
          isTreeChange(variables) && variables.scope === scope ? variables.paths : [],
        ),
      ),
    [pending, scope],
  );
}

const canonicalPaths = ({ scope, paths }: TreeChange) =>
  paths.map((path) => canonicalPath(scope, path));

export const documentMutations = {
  /** Delete documents (`entry`) or directories (`dir`), refused for the other kind. */
  remove: () =>
    treeMutation("remove", (change: TreeChange & { expect: DeleteKind }) =>
      applyAndPublish(
        canonicalPaths(change).map((path) => ({ kind: "delete", path, expect: change.expect })),
      ),
    ),
  /**
   * Move each path to its *destination* in `destScope`, which is the source
   * scope for a rename or an in-place move.
   */
  move: () =>
    treeMutation(
      "move",
      ({
        scope,
        paths,
        destScope,
        destination,
      }: TreeChange & { destScope: string; destination: (path: string) => string }) =>
        applyAndPublish(
          paths.map((source) => ({
            kind: "move",
            source: canonicalPath(scope, source),
            destination: canonicalPath(destScope, destination(source)),
          })),
        ),
    ),
  /** Create the directories at `paths`, shown before the tree refresh lands. */
  createDir: () =>
    treeMutation("create-dir", async (change: TreeChange, client) => {
      await applyAndPublish(canonicalPaths(change).map((path) => ({ kind: "mkdir", path })));
      client.setQueryData(
        documentQueries.tree(change.scope).queryKey,
        (tree) =>
          tree && {
            ...tree,
            root: change.paths.reduce((root, path) => withDirectory(root, path), tree.root),
          },
      );
    }),
  /** Rechunk documents in a background job, their rows spinning until it settles. */
  rechunk: () =>
    treeMutation("rechunk", (change: TreeChange & { spec?: PipelineSpec }) =>
      runJob(() => rechunkDocuments(canonicalPaths(change), change.spec)),
    ),
  /** Reconvert documents from their originals in a background job. */
  reconvert: () =>
    treeMutation("reconvert", (change: TreeChange & { spec?: PipelineSpec; llm?: LlmConfig }) =>
      runJob(() => reconvertDocuments(canonicalPaths(change), change.spec, change.llm)),
    ),
  /**
   * Write a document's markdown inline rather than as a job, `create` refusing
   * an existing name.  Its dialog shows the failure, a name collision included.
   */
  write: () => ({
    ...treeMutation(
      "write",
      async (
        change: TreeChange & {
          content: string;
          chunking?: PipelineSpec["chunking"];
          mode?: "replace" | "create";
        },
        client,
      ) => {
        const [path] = canonicalPaths(change);
        await writeDocument(path, change.content, change.chunking, change.mode);
        await refreshTrees(client, [change.scope]);
      },
    ),
    meta: { inlineError: true },
  }),
  assetDescription: (path: string) =>
    mutationOptions({
      mutationKey: [...all, "asset-description", path],
      mutationFn: (change: AssetDescriptionChange): Promise<AssetEntry> => {
        switch (change.action) {
          case "save":
            return updateAssetDescription(path, change.name, change.description);
          case "generate":
            return generateAssetDescription(path, change.name, change.llm);
          case "delete":
            return deleteAssetDescription(path, change.name);
        }
      },
      meta: { invalidates: [documentQueries.assets(path).queryKey] },
    }),
};

/**
 * Refetch the shown workspace queries of *client* whenever the workspace may
 * have changed: the trees of the scopes that did, and everything else.
 * Returns the unsubscribe.
 */
export function syncWorkspaceQueries(client: QueryClient): () => void {
  return onWorkspaceStale((scopes) => {
    void refreshTrees(client, scopes);
    void client.invalidateQueries({
      queryKey: all,
      predicate: ({ queryKey }) => queryKey[1] !== "tree",
    });
  });
}
