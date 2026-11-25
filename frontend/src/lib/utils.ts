import { i18n } from "@/i18n";
import type { WorkspaceChanged } from "@/api/jobs";

export { cn } from "cn";

/** The product name, the same in every language. */
export const PRODUCT_NAME = "Hivegent";

/** Filename without its extension (the logical document stem). */
export function fileStem(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

/** An identifier such as `title_status` or `maxLimit` as a sentence-case label, acronyms kept. */
export function humanize(identifier: string): string {
  const words = identifier
    .replaceAll(/[_-]+/g, " ")
    .replaceAll(/([a-z\d])([A-Z])/g, "$1 $2")
    .trim()
    .split(/\s+/)
    .map((word) => (word.length > 1 && word === word.toUpperCase() ? word : word.toLowerCase()));
  const text = words.join(" ");

  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Last segment of a path (the file or directory name). */
export function basename(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

/** Parent directory of *path*, as `""` or a `dir/`-style prefix. */
export function parentDir(path: string): string {
  return path.slice(0, path.lastIndexOf("/") + 1);
}

/** Join *base* and *name* with a slash, either one empty yielding the other. */
export function joinPath(base: string, name: string): string {
  return base && name ? `${base}/${name}` : base || name;
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
 * Where the canonical *path* is once *changed* applied: carried to the
 * destination of the move whose source holds it, or null when a delete took it.
 * Every source and delete names the state before the change, so all apply at once.
 */
export function followPath(
  path: string,
  { moves, deletes }: Pick<WorkspaceChanged, "moves" | "deletes">,
): string | null {
  if (deletes.some((root) => below(path, root) !== null)) return null;

  for (const { source, destination } of moves) {
    const rest = below(path, source);
    if (rest !== null) return destination + rest;
  }

  return path;
}

/** *items* each mapped by *follow*, the null ones dropped, or *items* itself when none changed. */
export function followEach<T>(items: T[], follow: (item: T) => T | null): T[] {
  const next = items.flatMap((item) => follow(item) ?? []);

  return next.length === items.length && next.every((item, i) => item === items[i]) ? items : next;
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
  const protocol = URL.parse(value)?.protocol;

  return protocol === "http:" || protocol === "https:";
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

/**
 * The start of *text* that a line-clamped preview can show, so the preview
 * never lays out the thousands of lines of a long chunk it hides anyway.
 */
export function previewText(text: string, maxChars = 300): string {
  return text.trimStart().slice(0, maxChars);
}

/**
 * Scroll *container* so *element*, or a text range, sits centered in it, or starts at its top when taller.
 * Unlike `scrollIntoView`, it leaves every other ancestor alone, so no clipping one shifts.
 */
export function centerInScroller(
  element: Element | Range,
  container: HTMLElement,
  behavior: ScrollBehavior = "auto",
): void {
  const box = element.getBoundingClientRect();
  const top = box.top - container.getBoundingClientRect().top + container.scrollTop;
  container.scrollTo({
    top: top - Math.max(0, (container.clientHeight - box.height) / 2),
    behavior,
  });
}

export const isAbortError = (err: unknown): boolean =>
  err instanceof DOMException && err.name === "AbortError";

/** A thrown value's message, or a localized generic one when it is no `Error`. */
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : i18n.t(($) => $.common.states.unknownError);
}

/** *value* parsed when it is a JSON string, undefined when it does not parse, and as is otherwise. */
export function parseJson(value: unknown): unknown {
  if (typeof value !== "string") return value;

  try {
    return JSON.parse(value);
  } catch {
    return undefined;
  }
}
