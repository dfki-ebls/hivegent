import type { DirectoryEntry, DocumentInfo } from "@/lib/types";

export { cn, type ClassValue } from "cnfast";

/** Handbook URL from `VITE_DOCS_URL`, or `undefined` when no handbook is served,
 * which hides the "Documentation" link. */
export const DOCS_URL = import.meta.env.VITE_DOCS_URL;

/** Format a byte count as a human-readable file size. */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

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

/** Coerce an unknown thrown value into a human-readable message. */
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
