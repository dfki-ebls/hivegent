import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

import type { DirectoryEntry } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

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

/** Longest common parent directory of *paths*, as `""` or a `dir/`-style prefix. */
export function commonParentDir(paths: string[]): string {
  const parentOf = (path: string) => path.slice(0, path.lastIndexOf("/") + 1);
  return paths.map(parentOf).reduce(
    (prefix, dir) => {
      while (!dir.startsWith(prefix)) {
        prefix = parentOf(prefix.slice(0, -1));
      }
      return prefix;
    },
    parentOf(paths[0] ?? ""),
  );
}

/** Collect all file paths under a directory entry (recursive). */
export function collectFilePaths(entry: DirectoryEntry, out: string[] = []): string[] {
  if (entry.type === "file") {
    out.push(entry.path);
  } else {
    for (const child of entry.children ?? []) {
      collectFilePaths(child, out);
    }
  }
  return out;
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
