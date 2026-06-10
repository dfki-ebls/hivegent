/**
 * Normalize custom void markdown tags (`<cite>`, `<imgref>`) before rendering.
 *
 * These tags carry all their data in attributes and render as self-contained
 * markers, but markdown-to-jsx (Streamdown's parser) does not honor the
 * self-closing `/>` syntax for unknown elements: an unclosed `<cite ...>` or
 * `<cite ... />` stays open and swallows the following prose into its children.
 *
 * This pass rewrites every such tag to an explicitly closed, empty element
 * (`<cite ...></cite>`) and drops any stray closing tag, so a model may emit
 * the natural self-closing form and the parser still treats it as void.
 */
const VOID_TAGS = ["cite", "imgref"] as const;

const VOID_TAG_RE = new RegExp(
  `<(${VOID_TAGS.join("|")})\\b([^>]*?)\\s*/?>|</(?:${VOID_TAGS.join("|")})>`,
  "gi",
);

export function normalizeVoidTags(text: string): string {
  return text.replace(VOID_TAG_RE, (_match, tag?: string, attrs?: string) => {
    if (!tag) return "";
    const a = (attrs ?? "").trim();
    return a ? `<${tag} ${a}></${tag}>` : `<${tag}></${tag}>`;
  });
}
