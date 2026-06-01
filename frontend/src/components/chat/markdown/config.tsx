import type { ComponentProps } from "react";
import { cjk } from "@streamdown/cjk";
import { code } from "@streamdown/code";
import { createMathPlugin } from "@streamdown/math";
import { mermaid } from "@streamdown/mermaid";
import type { MarkdownToJSX } from "markdown-to-jsx";

import { WorkspaceImage } from "@/components/WorkspaceImage";

const math = createMathPlugin({ singleDollarTextMath: true });

export const STREAMDOWN_PLUGINS = { cjk, code, math, mermaid };

export const MARKDOWN_BASE_OPTIONS = {
  disableParsingRawHTML: true,
} satisfies MarkdownToJSX.Options;

export function workspaceMarkdownOptions(
  documentPath: string,
  groupId?: string,
): MarkdownToJSX.Options {
  return {
    ...MARKDOWN_BASE_OPTIONS,
    overrides: {
      img: {
        component: ({ src, alt, ...props }: ComponentProps<"img">) => (
          <WorkspaceImage
            src={src}
            alt={alt ?? undefined}
            documentPath={documentPath}
            groupId={groupId}
            {...props}
          />
        ),
      },
    },
  };
}
