/**
 * Normalize custom void markdown tags (`<cite>`, `<imgref>`) before rendering.
 *
 * These tags carry all their data in attributes and render as self-contained
 * markers, but Streamdown's parser mishandles them in two ways this pass fixes:
 *
 * 1. It does not honor the self-closing `/>` syntax for unknown elements: an
 *    unclosed `<cite ...>` or `<cite ... />` stays open and swallows the
 *    following prose into its children. We rewrite every such tag to an
 *    explicitly closed, empty element (`<cite ...></cite>`) and drop any stray
 *    closing tag, so a model may emit the natural self-closing form and the
 *    parser still treats it as void.
 * 2. A void tag glued to a closing code fence (` ```<cite ...>`) keeps the
 *    block open. CommonMark only closes a fence when nothing but whitespace
 *    follows it on the line, so the tag and the rest of the document are
 *    swallowed as code. We move a glued tag onto its own line so the fence
 *    closes and the citation renders after the block.
 */
const VOID_TAGS = ["cite", "imgref"] as const;

const VOID_TAG_RE = new RegExp(
  `<(${VOID_TAGS.join("|")})\\b([^>]*?)\\s*/?>|</(?:${VOID_TAGS.join("|")})>`,
  "gi",
);

/** The seam between a closing code fence (``` or ~~~) and a glued void tag. */
const FENCE_VOID_RE = new RegExp(
  String.raw`(?<=\`{3}|~{3})(?=<(?:${VOID_TAGS.join("|")})\b)`,
  "gi",
);

export function normalizeVoidTags(text: string): string {
  return text
    .replace(FENCE_VOID_RE, "\n")
    .replace(VOID_TAG_RE, (_match, tag?: string, attrs?: string) => {
      if (!tag) return "";
      const a = (attrs ?? "").trim();
      return a ? `<${tag} ${a}></${tag}>` : `<${tag}></${tag}>`;
    });
}
