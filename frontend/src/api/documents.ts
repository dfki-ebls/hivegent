import {
  hashKey,
  mutationOptions,
  type QueryClient,
  queryOptions,
  useMutationState,
} from "@tanstack/react-query";
import { z } from "zod";

import {
  encodeFilePath,
  jsonRequest,
  requestBlob,
  requestJson,
  requestText,
  requestVoid,
} from "@/api/client";
import { apiUrl } from "@/lib/health";
import {
  type JobView,
  JobViewSchema,
  type WorkspaceChanged,
  WorkspaceChangedSchema,
} from "@/api/jobs";
import { type DocumentKind, parseOriginal } from "@/lib/original-preview";
import { downloadBlob } from "@/lib/download";
import { below, followPath, joinPath } from "@/lib/utils";
import {
  awaitJobSettled,
  onWorkspaceStale,
  publishWorkspaceChanged,
  useJobsStore,
} from "@/stores/jobs-store";

const EntryKindSchema = z.enum(["user_markdown", "image", "video", "convertible", "binary_stub"]);
const EntryOriginSchema = z.enum(["upload", "collection", "extracted", "imported", "synced"]);
const EntryGeneratedBySchema = z.enum(["user", "converter", "vision", "stub"]);

/** A single chunk within a chunked document. */
const ChunkInfoSchema = z.object({
  text: z.string(),
  token_count: z.number(),
  start_index: z.number(),
  end_index: z.number(),
  start_line: z.number(),
  end_line: z.number(),
});
/** Response from the chunks endpoint. */
export const ChunkedDocumentResponseSchema = z.object({
  pipeline: z.string(),
  created_at: z.string(),
  size_bytes: z.number().nullable().optional(),
  chunks: z.array(ChunkInfoSchema),
  entry_kind: EntryKindSchema.optional(),
  stem_path: z.string().nullable().optional(),
  description_path: z.string().nullable().optional(),
  original_path: z.string().nullable().optional(),
  assets_dir: z.string().nullable().optional(),
  origin: EntryOriginSchema.optional(),
  generated_by: EntryGeneratedBySchema.optional(),
  mime: z.string().nullable().optional(),
  case_base_candidate: z.boolean().optional(),
});
export type ChunkedDocumentResponse = z.infer<typeof ChunkedDocumentResponseSchema>;

const AssetEntrySchema = z.object({
  name: z.string(),
  path: z.string(),
  description_path: z.string().nullable(),
  description: z.string(),
  size_bytes: z.number(),
  media_type: z.string().nullable().optional(),
});
export type AssetEntry = z.infer<typeof AssetEntrySchema>;

const AssetListResponseSchema = z.object({
  assets: z.array(AssetEntrySchema),
  assets_dir: z.string(),
});
type AssetListResponse = z.infer<typeof AssetListResponseSchema>;

/** Batch document line counts, keyed by the requested workspace path. */
const DocumentLineCountsResponseSchema = z.object({
  line_counts: z.record(z.string(), z.number()),
});

/** A file or directory entry in the document tree (recursive). */
export interface DirectoryEntry {
  type: "file" | "directory";
  name: string;
  path: string;
  size_bytes?: number | null;
  modified_at?: string | null;
  chunk_count?: number | null;
  has_original?: boolean;
  original_path?: string | null;
  assets_dir?: string | null;
  /** Whether it is a case base with a saved similarity configuration. */
  case_base?: boolean;
  /** Whether it may be a case base by cbrkit's rule, which offers its similarity. */
  case_base_candidate?: boolean;
  children?: DirectoryEntry[] | null;
}

const DirectoryEntrySchema: z.ZodType<DirectoryEntry> = z.object({
  type: z.enum(["file", "directory"]),
  name: z.string(),
  path: z.string(),
  size_bytes: z.number().nullable().optional(),
  modified_at: z.string().nullable().optional(),
  chunk_count: z.number().nullable().optional(),
  has_original: z.boolean().optional(),
  original_path: z.string().nullable().optional(),
  assets_dir: z.string().nullable().optional(),
  case_base: z.boolean().optional(),
  case_base_candidate: z.boolean().optional(),
  children: z
    .lazy(() => z.array(DirectoryEntrySchema))
    .nullable()
    .optional(),
});

/** Response from the directory tree endpoint. */
export const DirectoryTreeResponseSchema = z.object({
  root: DirectoryEntrySchema,
  total_files: z.number(),
  total_directories: z.number(),
});
export type DirectoryTreeResponse = z.infer<typeof DirectoryTreeResponseSchema>;

/**
 * Canonical scope of the caller's personal workspace.
 *
 * Scopes are the single source of truth for addressing a workspace: `~` for
 * the personal one, `@<group>` for a group, and the prefixes the server names
 * for the global one and the sources. The server lists the ones the user sees
 * with their kinds, labels, and grants (`WorkspaceInfo`), and the same string
 * keys the document store, flows through the API, and is what the backend
 * resolves.
 */
export const PERSONAL_SCOPE = "~";

/** Where every source's mirror is mounted as `/mnt/<id>`, the backend's `store.SOURCE_PREFIX`. */
const SOURCE_PREFIX = "/mnt/";

/**
 * Compose the canonical path of a document from its scope and local path
 * (e.g. `~/notes.md`, `@research/notes.md`). An empty local yields the bare
 * scope root, so this is the exact inverse of {@link splitScopePath}.
 */
export function canonicalPath(scope: string, local: string): string {
  return joinPath(scope, local);
}

/**
 * Split a canonical directory into its scope and workspace-relative subpath,
 * the inverse of {@link canonicalPath}. The scope root (`~`, `@group`,
 * `/srv`, `/mnt/<id>`) yields an empty local; `~/a/b` yields `{ scope: "~", local: "a/b" }`.
 * A scope leading with a slash keeps it, so `/srv/a` splits after `/srv` and
 * `/mnt/wiki/a` after `/mnt/wiki`.
 */
export function splitScopePath(canonical: string): { scope: string; local: string } {
  const start = canonical.startsWith(SOURCE_PREFIX)
    ? SOURCE_PREFIX.length
    : canonical.startsWith("/")
      ? 1
      : 0;
  const slash = canonical.indexOf("/", start);
  return slash === -1
    ? { scope: canonical, local: "" }
    : { scope: canonical.slice(0, slash), local: canonical.slice(slash + 1) };
}

/** Whether *path* names a document of one of *workspaces*, not a web page or a `/tmp` file. */
export function isWorkspacePath(path: string, workspaces: readonly { scope: string }[]): boolean {
  const { scope } = splitScopePath(path);

  return workspaces.some((workspace) => workspace.scope === scope);
}

/**
 * Human-readable breadcrumb for a canonical directory, its workspace named by
 * its label among *workspaces*, e.g. `@research/a/b` becomes "Research / a / b".
 * Shared by every surface that names the active target (upload area, create dialogs).
 */
export function formatTarget(
  target: string,
  workspaces: readonly { scope: string; label: string }[],
): string {
  const { scope, local } = splitScopePath(target);
  const scopeLabel = workspaces.find((workspace) => workspace.scope === scope)?.label ?? scope;
  return local ? `${scopeLabel} / ${local.replaceAll("/", " / ")}` : scopeLabel;
}

/** Options for document upload. */
interface UploadDocumentOptions {
  /** Replace a document at the path, which is refused with 412 otherwise. */
  overwrite?: boolean;
}

/** The multipart body of an upload, processed with the operator's pipelines. */
function uploadForm(file: File): FormData {
  const form = new FormData();
  form.append("file", file);

  return form;
}

/** An upload's body dated at the file's modification time, which the entry keeps. */
function datedUploadForm(file: File): FormData {
  const form = uploadForm(file);
  form.append("modified_at", new Date(file.lastModified).toISOString());

  return form;
}

/**
 * Upload or replace a document and start its background job; `filename` is a
 * canonical path (`~/…` or `@<group>/…`). Resolves once the bytes are received
 * with the job's initial snapshot, conversion and indexing then run off the
 * request and are observable through the `/jobs` feed.
 */
export async function uploadDocument(
  filename: string,
  file: File,
  options?: UploadDocumentOptions & { signal?: AbortSignal },
): Promise<JobView> {
  return requestJson(apiUrl(`/documents/${encodeFilePath(filename)}`), "upload", JobViewSchema, {
    method: "PUT",
    // A PUT replaces the document, unless it may only create one.
    headers: options?.overwrite ? undefined : { "If-None-Match": "*" },
    body: datedUploadForm(file),
    signal: options?.signal,
  });
}

/**
 * Write a markdown document's content; `filename` is a canonical path. Keeps the
 * document's original binary and assets and only rewrites the text and its
 * chunks. `mode` defaults to `"replace"` (overwrite or create); pass `"create"`
 * to reject an existing path with a 409 instead of overwriting it.
 */
async function writeDocument(
  filename: string,
  content: string,
  mode: "replace" | "create" = "replace",
): Promise<void> {
  return requestVoid(
    apiUrl(`/documents/${encodeFilePath(filename)}`),
    "save",
    jsonRequest("PATCH", { content, mode }),
  );
}

/**
 * Upload a collection ZIP and start its background job; `target` is the
 * canonical directory the archive lands under, a scope root (`~`, `@<group>`)
 * or a subdir (`~/projects`). Resolves with the job's initial snapshot; per-file
 * progress then arrives through the `/jobs` feed.
 */
export async function uploadCollection(
  target: string,
  file: File,
  options?: { signal?: AbortSignal },
): Promise<JobView> {
  return requestJson(
    apiUrl(`/documents/collections/${encodeFilePath(target)}`),
    "uploadCollection",
    JobViewSchema,
    {
      method: "POST",
      body: uploadForm(file),
      signal: options?.signal,
    },
  );
}

/** The content route of a canonical path, whose `/tmp` is the one of *conversationId*. */
function documentUrl(filepath: string, conversationId?: string | null): string {
  const url = apiUrl(`/documents/${encodeFilePath(filepath)}`);

  return conversationId ? `${url}?conversation_id=${conversationId}` : url;
}

export async function getDocumentContent(
  filename: string,
  conversationId?: string | null,
): Promise<string> {
  return requestText(documentUrl(filename, conversationId), "fetchDocumentContent");
}

/** Fetch a workspace or `/tmp` asset (e.g. an image) of *conversationId*. */
function fetchDocumentAsset(
  filepath: string,
  conversationId: string | null,
  signal?: AbortSignal,
): Promise<Blob> {
  return requestBlob(documentUrl(filepath, conversationId), "fetchDocumentAsset", { signal });
}

/** Download the original binary file for a document. */
async function downloadOriginal(filepath: string, signal?: AbortSignal): Promise<Blob> {
  return requestBlob(apiUrl(`/documents/original/${encodeFilePath(filepath)}`), "download", {
    signal,
  });
}

async function getDocumentChunks(filename: string): Promise<ChunkedDocumentResponse> {
  return requestJson(
    apiUrl(`/documents/chunks/${encodeFilePath(filename)}`),
    "fetchChunks",
    ChunkedDocumentResponseSchema,
  );
}

/** Batch-resolve document line counts; unknown paths are omitted from the map. */
async function getDocumentLineCounts(files: string[]): Promise<Record<string, number>> {
  if (files.length === 0) return {};

  return (
    await requestJson(
      apiUrl("/documents/line-counts"),
      "fetchLineCounts",
      DocumentLineCountsResponseSchema,
      jsonRequest("POST", { files }),
    )
  ).line_counts;
}

// Asset API functions

/** List assets for a document. */
async function listDocumentAssets(filename: string): Promise<AssetListResponse> {
  return requestJson(
    apiUrl(`/documents/assets/${encodeFilePath(filename)}`),
    "listAssets",
    AssetListResponseSchema,
  );
}

/** Update an asset's companion .md description. */
async function updateAssetDescription(
  filename: string,
  assetName: string,
  content: string,
): Promise<AssetEntry> {
  return requestJson(
    apiUrl(`/documents/assets/${encodeFilePath(filename)}`),
    "updateDescription",
    AssetEntrySchema,
    jsonRequest("PATCH", { asset_name: assetName, content }),
  );
}

/** Generate an asset's companion .md description with the vision model. */
async function generateAssetDescription(filename: string, assetName: string): Promise<AssetEntry> {
  return requestJson(
    apiUrl(`/documents/assets/${encodeFilePath(filename)}`),
    "generateDescription",
    AssetEntrySchema,
    jsonRequest("POST", { asset_name: assetName }),
  );
}

/** Delete an asset's companion .md description, keeping the asset itself. */
async function deleteAssetDescription(filename: string, assetName: string): Promise<AssetEntry> {
  return requestJson(
    apiUrl(
      `/documents/assets/${encodeFilePath(filename)}?asset_name=${encodeURIComponent(assetName)}`,
    ),
    "deleteDescription",
    AssetEntrySchema,
    { method: "DELETE" },
  );
}

/** Rechunk documents with new settings as a background job. */
function rechunkDocuments(files: string[]): Promise<JobView> {
  return requestJson(apiUrl("/documents/rechunk"), "rechunk", JobViewSchema, jsonRequest("POST", { files }));
}

/** Reconvert documents from their originals as a background job. */
function reconvertDocuments(files: string[]): Promise<JobView> {
  return requestJson(
    apiUrl("/documents/reconvert"),
    "reconvert",
    JobViewSchema,
    jsonRequest("POST", { files }),
  );
}

// --- Workspace changes ---

/** What a delete expects its path to be, refused when it is the other kind. */
type DeleteKind = "entry" | "dir";

/** One operation of {@link applyChanges}, every path canonical (`~/a.md`, `@team/b`). */
export type ChangeOperation =
  | { kind: "move"; source: string; destination: string }
  | { kind: "delete"; path: string; expect: DeleteKind }
  | { kind: "mkdir"; path: string };

/**
 * Move, delete, and create documents and directories as one changeset. All of
 * them land or none does, and a refusal rejects with the message naming its path.
 * What the backend applied is published here at once, since the feed may be
 * reconnecting, and reaches every other tab through the feed.
 */
async function applyChanges(operations: ChangeOperation[]): Promise<void> {
  publishWorkspaceChanged(
    await requestJson(
      apiUrl("/changes"),
      "applyChanges",
      WorkspaceChangedSchema,
      jsonRequest("POST", { operations }),
    ),
  );
}

// Directory tree

/** Fetch a workspace directory tree by its scope (`~`, `@<group>`, `/srv`). */
export async function getDirectories(
  scope: string,
  signal?: AbortSignal,
): Promise<DirectoryTreeResponse> {
  return requestJson(
    apiUrl(`/directories/${encodeFilePath(scope)}`),
    "fetchDirectories",
    DirectoryTreeResponseSchema,
    { signal },
  );
}

/** Delete all documents, chunks, originals, and the search index in the workspace of *scope*. */
export async function deleteAllDocuments(scope: string): Promise<void> {
  return requestVoid(apiUrl(`/workspaces/${encodeFilePath(scope)}`), "deleteDocuments", {
    method: "DELETE",
  });
}

/** Collect all file paths under a directory entry (recursive). */
export function collectFilePaths(entry: DirectoryEntry): string[] {
  return entry.type === "file" ? [entry.path] : (entry.children ?? []).flatMap(collectFilePaths);
}

/** Return a copy of *root* with an empty directory at *path* (and any missing
 * ancestors) grafted in.
 *
 * Lets a create land in the view without waiting for a fresh tree from the
 * server, whose walk covers the whole workspace. Parents are created
 * implicitly and an existing segment is reused, matching the backend, so it is
 * idempotent and the refresh that follows simply confirms it. Placement is not
 * its concern: the tree view sorts children as it renders them. */
function withDirectory(root: DirectoryEntry, path: string): DirectoryEntry {
  const segments = path.split("/").filter(Boolean);
  if (segments.length === 0) return root;

  const graft = (node: DirectoryEntry, [name, ...rest]: string[]): DirectoryEntry => {
    if (name === undefined) return node;

    const children = node.children ?? [];
    const existing = children.find((c) => c.type === "directory" && c.name === name);
    const created: DirectoryEntry = existing ?? {
      type: "directory",
      name,
      path: joinPath(node.path, name),
      children: [],
    };
    const updated = rest.length > 0 ? graft(created, rest) : created;
    return {
      ...node,
      children: existing
        ? children.map((c) => (c === existing ? updated : c))
        : [...children, updated],
    };
  };

  return graft(root, segments);
}

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
   * A workspace asset of one of *workspaces*, or a `/tmp` one of *conversationId*, such as an
   * image, as an object URL. The feed keeps workspace assets fresh, while a `/tmp` one
   * refetches when shown again, since `/tmp` changes unannounced.
   */
  asset: (path: string, conversationId: string | null, workspaces: readonly { scope: string }[]) =>
    queryOptions({
      queryKey: [...all, "asset", path, conversationId],
      queryFn: async ({ signal }) =>
        URL.createObjectURL(await fetchDocumentAsset(path, conversationId, signal)),
      staleTime: isWorkspacePath(path, workspaces) ? Infinity : 0,
      meta: { objectUrl: true },
    }),
  /** The original of a workspace document, parsed for its *kind* of preview. */
  original: <K extends DocumentKind>(path: string, kind: K) =>
    queryOptions({
      queryKey: [...all, "original", path, kind],
      queryFn: async ({ signal }) =>
        parseOriginal[kind](await downloadOriginal(path, signal), signal),
      meta: { objectUrl: kind === "pdf" },
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
  | { action: "generate"; name: string }
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

/** A change to the trees, whose rows at the canonical `paths` spin while it runs. */
interface TreeChange {
  paths: readonly string[];
}

function isTreeChange(variables: unknown): variables is TreeChange {
  return (
    typeof variables === "object" &&
    variables !== null &&
    "paths" in variables &&
    Array.isArray(variables.paths)
  );
}

/** The workspaces the canonical *paths* lie in, each once. */
function scopesOf(paths: readonly string[]): string[] {
  return [...new Set(paths.map((path) => splitScopePath(path).scope))];
}

/**
 * A mutation of the trees its `paths` lie in.  This tab publishes what it did
 * from the answer and the feed announces it to every other tab, which refreshes
 * every tree it touched, and the mutation stays pending until those reads land, joining any in flight, so
 * its rows spin until the trees show the outcome.  A failure refreshes too,
 * since the view may hold an entry the backend no longer knows.
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
      } finally {
        await Promise.all(
          scopesOf(variables.paths).map((scope) =>
            client.invalidateQueries({ queryKey: treeKey(scope) }, { cancelRefetch: false }),
          ),
        );
      }
    },
  });
}

/** Submit a background job, show it in the tray, and wait for it to settle. */
async function runJob(submit: () => Promise<JobView>): Promise<void> {
  const job = await submit();
  useJobsStore.getState().upsert(job);
  await awaitJobSettled(job.id);
}

/** Canonical paths that a pending mutation is changing, until their tree refreshes. */
export function useMutatingPaths(): ReadonlySet<string> {
  const pending = useMutationState({
    filters: { mutationKey: [...all, "tree"], status: "pending" },
    select: ({ state }) => state.variables,
  });

  return new Set(pending.flatMap((variables) => (isTreeChange(variables) ? variables.paths : [])));
}

/** Every mutation takes canonical paths, so one request may span workspaces. */
export const documentMutations = {
  /** Delete documents, and the directories among them as `dirs`, each refused for the other kind. */
  remove: () =>
    treeMutation("remove", ({ paths, dirs }: TreeChange & { dirs: ReadonlySet<string> }) =>
      applyChanges(
        paths.map((path) => ({ kind: "delete", path, expect: dirs.has(path) ? "dir" : "entry" })),
      ),
    ),
  /** Move each path to its canonical *destination*, in the same workspace or another one. */
  move: () =>
    treeMutation(
      "move",
      ({ paths, destination }: TreeChange & { destination: (path: string) => string }) =>
        applyChanges(
          paths.map((source) => ({ kind: "move", source, destination: destination(source) })),
        ),
    ),
  /** Create the directories at `paths`, shown before the tree refresh lands. */
  createDir: () =>
    treeMutation("create-dir", async ({ paths }: TreeChange, client) => {
      await applyChanges(paths.map((path) => ({ kind: "mkdir", path })));

      const byScope = Map.groupBy(paths.map(splitScopePath), ({ scope }) => scope);

      for (const [scope, dirs] of byScope) {
        client.setQueryData(
          documentQueries.tree(scope).queryKey,
          (tree) =>
            tree && {
              ...tree,
              root: dirs.reduce((root, { local }) => withDirectory(root, local), tree.root),
            },
        );
      }
    }),
  /** Rechunk documents in a background job, their rows spinning until it settles. */
  rechunk: () =>
    treeMutation("rechunk", ({ paths }: TreeChange) => runJob(() => rechunkDocuments([...paths]))),
  /** Download originals, each at its canonical `path` under its `name`, one at a time so the browser sees no burst. */
  downloadOriginals: () =>
    mutationOptions({
      mutationKey: [...all, "download-originals"],
      mutationFn: async (originals: readonly { path: string; name: string }[]) => {
        for (const { path, name } of originals) downloadBlob(await downloadOriginal(path), name);
      },
    }),
  /** Reconvert documents from their originals in a background job. */
  reconvert: () =>
    treeMutation("reconvert", ({ paths }: TreeChange) =>
      runJob(() => reconvertDocuments([...paths])),
    ),
  /**
   * Write a document's markdown inline rather than as a job, `create` refusing
   * an existing name.  Its dialog shows the failure, a name collision included.
   */
  write: () => ({
    ...treeMutation(
      "write",
      (
        change: TreeChange & {
          content: string;
          mode?: "replace" | "create";
        },
      ) => writeDocument(change.paths[0] ?? "", change.content, change.mode),
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
            return generateAssetDescription(path, change.name);
          case "delete":
            return deleteAssetDescription(path, change.name);
        }
      },
      meta: { invalidates: [documentQueries.assets(path).queryKey] },
    }),
};

/** Whether *changed* moved, deleted, or replaced what lies at the canonical *path*. */
function touches(changed: WorkspaceChanged, path: string): boolean {
  return (
    followPath(path, changed) !== path ||
    changed.moves.some(({ destination }) => below(path, destination) !== null)
  );
}

/**
 * Refetch the shown workspace queries of *client* whenever the workspace may
 * have changed: the trees of the scopes that did, and the queries of paths in
 * them, so a burst of commits to one scope leaves the others' previews alone.
 * Assets refetch only for a path a move or delete touched, since a change rarely
 * rewrites an image in place and refetching every shown one is costly.
 * Returns the unsubscribe.
 */
export function syncWorkspaceQueries(client: QueryClient): () => void {
  return onWorkspaceStale((changed) => {
    void refreshTrees(client, changed === "all" ? "all" : changed.scopes);
    void client.invalidateQueries({
      queryKey: all,
      predicate: ({ queryKey: [, kind, path] }) =>
        kind !== "tree" &&
        (changed === "all" ||
          (typeof path === "string" &&
            (kind === "asset"
              ? touches(changed, path)
              : changed.scopes.includes(splitScopePath(path).scope)))),
    });
  });
}
