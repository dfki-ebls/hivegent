/**
 * Normalize LaTeX math so remark-math + KaTeX can render it.
 *
 * 1. Convert `\(...\)` / `\[...\]` to `$...$` / `$$...$$` (remark-math only reads
 *    dollars). A literal escaped backslash before the bracket (`\\[`, `\\(`) is
 *    left alone.
 * 2. Repair over-escaped commands inside each dollar span. Models that follow the
 *    prompt emit `$...$` / `$$...$$` directly, so broken `\\text`, `\\frac` (which
 *    KaTeX reads as a line break plus literal letters) live inside spans the
 *    conversion step never touches -- this pass is the actual fix. Collapsing `\\`
 *    to `\` before a letter repairs the command while leaving `\\` row separators
 *    (always followed by whitespace, `&`, or `\end`) untouched.
 *
 * Inline math (`$...$`) requires `singleDollarTextMath: true` in remark-math.
 */

/** `\[...\]` display delimiter (skips a literal escaped `\\[`). */
const DISPLAY_DELIMITER = /(?<!\\)\\\[([\s\S]*?)(?<!\\)\\\]/g;
/** `\(...\)` inline delimiter (skips a literal escaped `\\(`). */
const INLINE_DELIMITER = /(?<!\\)\\\(([\s\S]*?)(?<!\\)\\\)/g;
/** A `\\` that wrongly escapes a command name (`\\text`). */
const OVER_ESCAPED_COMMAND = /\\\\(?=[a-zA-Z])/g;
/** A `$$...$$` display span. */
const DISPLAY_SPAN = /\$\$([\s\S]*?)\$\$/g;
/** A `$$...$$` display span or a `$...$` inline span. */
const MATH_SPAN = /\$\$([\s\S]*?)\$\$|\$([^\n$]+?)\$/g;

/** Collapse over-escaped commands (`\\text` -> `\text`) in a span body. */
const fixCommands = (body: string | undefined): string =>
  (body ?? "").replace(OVER_ESCAPED_COMMAND, "\\");

/** Normalize display math (`\[...\]` and `$$...$$`). */
export function normalizeDisplayMath(text: string): string {
  return text
    .replace(DISPLAY_DELIMITER, "$$$$$1$$$$")
    .replace(DISPLAY_SPAN, (_m, body) => `$$${fixCommands(body)}$$`);
}

/** Normalize inline and display math. */
export function normalizeMath(text: string): string {
  return text
    .replace(DISPLAY_DELIMITER, "$$$$$1$$$$")
    .replace(INLINE_DELIMITER, "$$$1$$")
    .replace(MATH_SPAN, (_m, display, inline) =>
      display !== undefined ? `$$${fixCommands(display)}$$` : `$${fixCommands(inline)}$`,
    );
}
