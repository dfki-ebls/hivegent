import {
  hashKey,
  mutationOptions,
  type QueryClient,
  queryOptions,
  useMutationState,
} from "@tanstack/react-query";
import { useMemo } from "react";
import { z } from "zod";

import {
  apiUrl,
  checkedResponse,
  encodeFilePath,
  jsonRequest,
  requestBlob,
  requestJson,
  requestText,
  requestVoid,
} from "@/api/client";
import { type JobView, JobViewSchema, type WorkspaceChanged, WorkspaceChangedSchema } from "@/api/jobs";
import type { PipelineSpec } from "@/api/pipelines";
import type { LlmConfig } from "@/api/settings";
import { type DocumentKind, parseOriginal } from "@/lib/original-preview";
import { joinPath } from "@/lib/utils";
import {
  awaitJobSettled,
  onWorkspaceStale,
  publishWorkspaceChanged,
  useJobsStore,
} from "@/stores/jobs-store";

const EntryKindSchema = z.enum(["user_markdown", "image", "video", "convertible", "binary_stub"]);
const EntryOriginSchema = z.enum(["upload", "collection", "extracted", "imported"]);
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
const ChunkedDocumentResponseSchema = z.object({
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
type AssetEntry = z.infer<typeof AssetEntrySchema>;

const AssetListResponseSchema = z.object({
  assets: z.array(AssetEntrySchema),
  assets_dir: z.string(),
});
type AssetListResponse = z.infer<typeof AssetListResponseSchema>;

/** A flat view of a file entry of the directory tree, see `treeDocuments`. */
export interface DocumentInfo {
  filename: string;
  display_name: string;
  size_bytes: number;
  modified_at: string;
  chunk_count?: number | null;
  has_original: boolean;
  original_path?: string | null;
  assets_dir?: string | null;
}

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
  children: z
    .lazy(() => z.array(DirectoryEntrySchema))
    .nullable()
    .optional(),
});

/** Response from the directory tree endpoint. */
const DirectoryTreeResponseSchema = z.object({
  root: DirectoryEntrySchema,
  total_files: z.number(),
  total_directories: z.number(),
});
export type DirectoryTreeResponse = z.infer<typeof DirectoryTreeResponseSchema>;

/**
 * Canonical scope of the caller's personal workspace.
 *
 * Scopes are the single source of truth for addressing a workspace: `~` for
 * the personal one, `@<group>` for a group (see {@link groupScope}). The same
 * string keys the document store, flows through the API, and is what the
 * backend resolves — there is no separate "personal vs group" representation.
 */
export const PERSONAL_SCOPE = "~";

/**
 * Canonical scope of a group's workspace.
 *
 * Always built from `GroupInfo.id`, never the display name: the id is the
 * group's only identity, so a path keeps addressing the same workspace after
 * a rename. `GroupInfo.name` is for labels.
 */
export function groupScope(groupId: string): string {
  return `@${groupId}`;
}

/**
 * Compose the canonical path of a document from its scope and local path
 * (e.g. `~/notes.md`, `@research/notes.md`). An empty local yields the bare
 * scope root, so this is the exact inverse of {@link splitScopePath}.
 */
export function canonicalPath(scope: string, local: string): string {
  return joinPath(scope, local);
}

/**
 * Split a canonical directory into its scope and workspace-relative subpath —
 * the inverse of {@link canonicalPath}. The scope root (`~`, `@group`) yields an
 * empty local; `~/a/b` yields `{ scope: "~", local: "a/b" }`.
 */
export function splitScopePath(canonical: string): { scope: string; local: string } {
  const slash = canonical.indexOf("/");
  return slash === -1
    ? { scope: canonical, local: "" }
    : { scope: canonical.slice(0, slash), local: canonical.slice(slash + 1) };
}

/** Whether *path* names a document of a user or group workspace, not a web page or a `/tmp` file. */
export function isWorkspacePath(path: string): boolean {
  const { scope } = splitScopePath(path);

  return scope === PERSONAL_SCOPE || (scope.startsWith("@") && scope.length > 1);
}

/**
 * Human-readable breadcrumb for a canonical directory, e.g. `~/a/b` becomes
 * "~ / a / b" and `@research` becomes "research". Shared by every surface
 * that names the active target (upload area, create dialogs).
 */
export function formatTarget(target: string): string {
  const { scope, local } = splitScopePath(target);
  const scopeLabel = scope.startsWith("@") ? scope.slice(1) : scope;
  return local ? `${scopeLabel} / ${local.replaceAll("/", " / ")}` : scopeLabel;
}

/** Check if a file requires conversion (anything that is not already markdown). */
export function requiresConversion(filename: string): boolean {
  const ext = `.${filename.split(".").pop()?.toLowerCase() ?? ""}`;
  return ext !== ".md";
}

/** Options for document upload. */
interface UploadDocumentOptions {
  spec?: PipelineSpec;
  llm?: LlmConfig;
  overwrite?: boolean;
}

/** The multipart body of an upload, with the settings its processing runs with. */
function uploadForm(file: File, spec?: PipelineSpec, llm?: LlmConfig): FormData {
  const form = new FormData();
  form.append("file", file);

  if (spec) form.append("pipeline_spec", JSON.stringify(spec));

  if (llm) form.append("llm_config", JSON.stringify(llm));

  return form;
}

/**
 * Upload or replace a document and start its background job; `filename` is a
 * canonical path (`~/…` or `@<group>/…`). Resolves once the bytes are received
 * with the job's initial snapshot — conversion and indexing then run off the
 * request and are observable through the `/jobs` feed.
 */
export async function uploadDocument(
  filename: string,
  file: File,
  options?: UploadDocumentOptions & { signal?: AbortSignal },
): Promise<JobView> {
  const body = uploadForm(
    file,
    options?.spec,
    requiresConversion(filename) ? options?.llm : undefined,
  );

  if (options?.overwrite) body.append("overwrite", "true");

  return requestJson(
    apiUrl(`/documents/${encodeFilePath(filename)}`),
    "upload",
    JobViewSchema,
    { method: "PUT", body, signal: options?.signal },
  );
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
  chunking?: PipelineSpec["chunking"],
  mode: "replace" | "create" = "replace",
): Promise<void> {
  return requestVoid(
    apiUrl(`/documents/${encodeFilePath(filename)}`),
    "save",
    jsonRequest("PATCH", { content, mode, chunking: chunking ?? null }),
  );
}

/** Options for collection upload (ZIP or directory). */
export interface UploadCollectionOptions {
  spec?: PipelineSpec;
  llm?: LlmConfig;
}

/**
 * Upload a collection ZIP and start its background job; `target` is the
 * canonical directory the archive lands under — a scope root (`~`, `@<group>`)
 * or a subdir (`~/projects`). Resolves with the job's initial snapshot; per-file
 * progress then arrives through the `/jobs` feed.
 */
export async function uploadCollection(
  target: string,
  file: File,
  options?: UploadCollectionOptions & { signal?: AbortSignal },
): Promise<JobView> {
  return requestJson(
    apiUrl(`/documents/collections/${encodeFilePath(target)}`),
    "uploadCollection",
    JobViewSchema,
    { method: "POST", body: uploadForm(file, options?.spec, options?.llm), signal: options?.signal },
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

/** Fetch a workspace or `/tmp` asset (e.g. image) as a blob URL for display. */
export async function fetchDocumentAsset(
  filepath: string,
  signal?: AbortSignal,
  conversationId?: string | null,
): Promise<string> {
  const blob = await (
    await checkedResponse(documentUrl(filepath, conversationId), "fetchDocumentAsset", { signal })
  ).blob();
  return URL.createObjectURL(blob);
}

/** Download the original binary file for a document. */
export async function downloadOriginal(filepath: string, signal?: AbortSignal): Promise<Blob> {
  return requestBlob(
    apiUrl(`/documents/original/${encodeFilePath(filepath)}`),
    "download",
    { signal },
  );
}

/**
 * Replace the original binary file and reconvert the document as a background
 * job; resolves with the job's initial snapshot. Progress arrives via the feed.
 */
export async function replaceOriginal(
  filepath: string,
  file: File,
  spec?: PipelineSpec,
  llm?: LlmConfig,
): Promise<JobView> {
  return requestJson(
    apiUrl(`/documents/original/${encodeFilePath(filepath)}`),
    "replaceOriginal",
    JobViewSchema,
    { method: "PUT", body: uploadForm(file, spec, llm) },
  );
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
async function generateAssetDescription(
  filename: string,
  assetName: string,
  llm?: LlmConfig,
): Promise<AssetEntry> {
  return requestJson(
    apiUrl(`/documents/assets/${encodeFilePath(filename)}`),
    "generateDescription",
    AssetEntrySchema,
    jsonRequest("POST", { asset_name: assetName, llm: llm ?? {} }),
  );
}

/** Delete an asset's companion .md description, keeping the asset itself. */
async function deleteAssetDescription(
  filename: string,
  assetName: string,
): Promise<AssetEntry> {
  return requestJson(
    apiUrl(`/documents/assets/${encodeFilePath(filename)}?asset_name=${encodeURIComponent(assetName)}`),
    "deleteDescription",
    AssetEntrySchema,
    { method: "DELETE" },
  );
}

/** Rechunk documents with new settings as a background job. */
function rechunkDocuments(files: string[], spec?: PipelineSpec): Promise<JobView> {
  return requestJson(
    apiUrl("/documents/rechunk"),
    "rechunk",
    JobViewSchema,
    jsonRequest("POST", { files, pipeline: spec ?? {} }),
  );
}

/** Reconvert documents from their originals as a background job. */
function reconvertDocuments(
  files: string[],
  spec?: PipelineSpec,
  llm?: LlmConfig,
): Promise<JobView> {
  return requestJson(
    apiUrl("/documents/reconvert"),
    "reconvert",
    JobViewSchema,
    jsonRequest("POST", { files, pipeline: spec ?? {}, llm: llm ?? {} }),
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
 * Resolves with the scopes it changed and what moved and went as the backend
 * resolved it, which can differ from what was sent (a move into an existing
 * folder, an entry's original).
 */
export async function applyChanges(operations: ChangeOperation[]): Promise<WorkspaceChanged> {
  return requestJson(
    apiUrl("/changes"),
    "applyChanges",
    WorkspaceChangedSchema,
    jsonRequest("POST", { operations }),
  );
}

// Directory tree

/** Fetch a workspace directory tree; `scope` is `~` (personal) or `@<group>`. */
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

/**
 * Delete all documents, chunks, originals, and the search index in a workspace;
 * `scope` is `~` (personal) or `@<group>`.
 */
export async function deleteAllDocuments(scope: string): Promise<void> {
  return requestVoid(apiUrl(`/documents/${encodeFilePath(scope)}`), "deleteDocuments", {
    method: "DELETE",
  });
}

/** Collect all file entries under a directory entry (recursive). */
export function collectFileEntries(
  entry: DirectoryEntry,
  out: DirectoryEntry[] = [],
): DirectoryEntry[] {
  if (entry.type === "file") {
    out.push(entry);
  } else {
    for (const child of entry.children ?? []) {
      collectFileEntries(child, out);
    }
  }
  return out;
}

/** Collect all file paths under a directory entry (recursive). */
export function collectFilePaths(entry: DirectoryEntry): string[] {
  return collectFileEntries(entry).map((file) => file.path);
}

/** Derive the flat document listing from a directory tree's file entries. */
export function treeDocuments(root: DirectoryEntry): DocumentInfo[] {
  return collectFileEntries(root).map((file) => ({
    filename: file.path,
    display_name: file.name,
    size_bytes: file.size_bytes ?? 0,
    modified_at: file.modified_at ?? "",
    chunk_count: file.chunk_count,
    has_original: file.has_original ?? false,
    original_path: file.original_path,
    assets_dir: file.assets_dir,
  }));
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
  /** The original of a workspace document, parsed for its *kind* of preview. */
  original: <K extends DocumentKind>(path: string, kind: K) =>
    queryOptions({
      queryKey: [...all, "original", path, kind],
      queryFn: async ({ signal }) => parseOriginal[kind](await downloadOriginal(path, signal)),
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
type AssetDescriptionChange =
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
        await writeDocument(canonicalPath(change.scope, change.paths[0] ?? ""), change.content, change.chunking, change.mode);
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
