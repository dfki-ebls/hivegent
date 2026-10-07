import { i18n } from "@/i18n";
import type { DirectoryEntry, DocumentInfo, PathChanges } from "@/lib/types";

export { cn, type ClassValue } from "cn";

/** The product name, the same in every language. */
export const PRODUCT_NAME = "Hivegent";

/** Handbook URL from `VITE_DOCS_URL`, or `undefined` when no handbook is served,
 * which hides the "Documentation" link. */
export const DOCS_URL = import.meta.env.VITE_DOCS_URL;

/** Filename without its extension (the logical document stem). */
export function fileStem(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

/** Last segment of a path (the file or directory name). */
export function basename(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

/** Parent directory of *path*, as `""` or a `dir/`-style prefix. */
export function parentDir(path: string): string {
  return path.slice(0, path.lastIndexOf("/") + 1);
}

/**
 * The part of *path* below *root*, empty for *root* itself and starting with a
 * slash for its descendants (the directory entry `~/a/` of root `~/a` too),
 * or null when *path* lies elsewhere.
 * Matching on a segment boundary keeps `~/docs` off `~/docs-old`.
 */
export function below(path: string, root: string): string | null {
  return path === root || path.startsWith(`${root}/`) ? path.slice(root.length) : null;
}

/**
 * Where the canonical *path* is once *changes* applied: carried to the
 * destination of the move whose source holds it, or null when a delete took it.
 * Every source and delete names the state before the commit, so all apply at once.
 */
export function followPath(path: string, { moves, deletes }: PathChanges): string | null {
  if (deletes.some((root) => below(path, root) !== null)) return null;

  for (const { source, destination } of moves) {
    const rest = below(path, source);
    if (rest !== null) return destination + rest;
  }

  return path;
}

/** *paths* each mapped by *follow*, the null ones dropped, or *paths* itself when none changed. */
export function followEach(paths: string[], follow: (path: string) => string | null): string[] {
  const next = paths.flatMap((path) => follow(path) ?? []);

  return next.length === paths.length && next.every((path, i) => path === paths[i]) ? paths : next;
}

/** Longest common parent directory of *paths*, as `""` or a `dir/`-style prefix. */
export function commonParentDir(paths: string[]): string {
  return paths.map(parentDir).reduce(
    (prefix, dir) => {
      while (!dir.startsWith(prefix)) {
        prefix = parentDir(prefix.slice(0, -1));
      }
      return prefix;
    },
    parentDir(paths[0] ?? ""),
  );
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
    kind: "document" as const,
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
export function withDirectory(root: DirectoryEntry, path: string): DirectoryEntry {
  const segments = path.split("/").filter(Boolean);
  if (segments.length === 0) return root;

  const graft = (node: DirectoryEntry, [name, ...rest]: string[]): DirectoryEntry => {
    const children = node.children ?? [];
    const existing = children.find((c) => c.type === "directory" && c.name === name);
    const created: DirectoryEntry = existing ?? {
      type: "directory",
      name,
      path: node.path ? `${node.path}/${name}` : name,
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

/** Convert a snake_case string to Title Case. */
export function snakeCaseToTitleCase(s: string): string {
  return s
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Check whether a filename is an external web URL. */
export function isWebUrl(value: string): boolean {
  return value.startsWith("http://") || value.startsWith("https://");
}

/** Format a URL for display as `hostname/path`. Falls back to the raw value. */
export function formatWebUrl(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname + u.pathname;
  } catch {
    return url;
  }
}

export const isAbortError = (err: unknown): boolean =>
  err instanceof DOMException && err.name === "AbortError";

/** A thrown value's message, or a localized generic one when it is no `Error`. */
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : i18n.t(($) => $.common.states.unknownError);
}
