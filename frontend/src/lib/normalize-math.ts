/**
 * Normalize LaTeX display math delimiters to dollar-sign notation.
 *
 * Converts `\[...\]` to `$$...$$` so that remark-math can parse them.
 * Escaped backslashes (`\\[`) are left untouched.
 */
export function normalizeDisplayMathDelimiters(text: string): string {
  return text.replace(/(?<!\\)\\\[([\s\S]*?)(?<!\\)\\\]/g, "$$$$$1$$$$");
}

/**
 * Normalize all LaTeX math delimiters to dollar-sign notation.
 *
 * Converts `\(...\)` to `$...$` (inline) and `\[...\]` to `$$...$$` (display)
 * so that remark-math can parse them. Escaped backslashes (`\\(`, `\\[`) are
 * left untouched.
 *
 * Inline math (`$...$`) requires `singleDollarTextMath: true` in remark-math.
 */
export function normalizeMathDelimiters(text: string): string {
  return normalizeDisplayMathDelimiters(text).replace(
    /(?<!\\)\\\(([\s\S]*?)(?<!\\)\\\)/g,
    "$$$1$$"
  );
}
