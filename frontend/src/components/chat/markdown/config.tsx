import type { ComponentProps } from "react";
import { cjk } from "@streamdown/cjk";
import { createMathPlugin } from "@streamdown/math";
import type { MarkdownToJSX } from "markdown-to-jsx";
import type { DiagramPlugin } from "streamdown";

import { WorkspaceImage } from "@/components/WorkspaceImage";

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

export const MARKDOWN_BASE_OPTIONS = {
  disableParsingRawHTML: true,
} satisfies MarkdownToJSX.Options;

export function workspaceMarkdownOptions(documentPath: string): MarkdownToJSX.Options {
  return {
    ...MARKDOWN_BASE_OPTIONS,
    overrides: {
      img: {
        component: ({ src, alt, ...props }: ComponentProps<"img">) => (
          <WorkspaceImage src={src} alt={alt ?? undefined} documentPath={documentPath} {...props} />
        ),
      },
    },
  };
}
