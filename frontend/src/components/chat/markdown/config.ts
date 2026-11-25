import { cjk } from "@streamdown/cjk";
import { createMathPlugin } from "@streamdown/math";
import {
  type AllowedTags,
  type DiagramPlugin,
  defaultRehypePlugins,
  type StreamdownProps,
} from "streamdown";

const math = createMathPlugin({ singleDollarTextMath: true });

/**
 * Mermaid is larger than the rest of the chat, so it loads with the first diagram.
 * The chat sets no Mermaid config, so the plugin keeps its defaults.
 */
const mermaid: DiagramPlugin = {
  name: "mermaid",
  type: "diagram",
  language: "mermaid",
  getMermaid: () => ({
    initialize: () => {},
    render: async (id, source) =>
      (await import("@streamdown/mermaid")).mermaid.getMermaid().render(id, source),
  }),
};

/** Code fences render as a `CodeBlock` (see `MarkdownText`), so there is no code plugin. */
export const STREAMDOWN_PLUGINS = { cjk, math, mermaid };

type Pluggable = NonNullable<StreamdownProps["rehypePlugins"]>[number];

/** A default rehype step of Streamdown as `[plugin, options]`. */
function defaultStep(
  name: "raw" | "sanitize" | "undoubleClobberPrefix" | "harden",
): Extract<Pluggable, unknown[]> {
  const step = defaultRehypePlugins[name];

  if (typeof step === "function") return [step];

  if (!Array.isArray(step)) {
    throw new TypeError(`Streamdown has no default rehype step "${name}"`);
  }

  return step;
}

const [rehypeSanitize, sanitizeSchema] = defaultStep("sanitize");
const [rehypeHarden, hardenOptions] = defaultStep("harden");

/** The elements markdown itself produces, so raw HTML in model output renders nothing markdown could not. */
const MARKDOWN_TAGS = [
  "a",
  "blockquote",
  "br",
  "code",
  "del",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "img",
  "input",
  "li",
  "ol",
  "p",
  "pre",
  "section",
  "strong",
  "sup",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "ul",
];

/**
 * Streamdown's sanitizer limited to markdown elements plus *tags*,
 * and its URL hardening without images.
 * A remote image in model output would load on render and carry conversation data out in its URL,
 * so pictures only come from `<imgref>`, which fetches workspace assets from the API.
 * Ids and names keep the sanitizer's prefix, so model output cannot clobber globals.
 * No in-page anchor breaks, since every link opens in a new tab behind Streamdown's confirmation.
 */
export function rehypePlugins(tags: AllowedTags = {}): Pluggable[] {
  return [
    defaultStep("raw"),
    [
      rehypeSanitize,
      {
        ...sanitizeSchema,
        tagNames: [...MARKDOWN_TAGS, ...Object.keys(tags)],
        attributes: { ...sanitizeSchema.attributes, ...tags },
      },
    ],
    defaultStep("undoubleClobberPrefix"),
    [
      rehypeHarden,
      {
        ...hardenOptions,
        allowedImagePrefixes: [],
        allowDataImages: false,
        imageBlockPolicy: "text-only",
      },
    ],
  ];
}
