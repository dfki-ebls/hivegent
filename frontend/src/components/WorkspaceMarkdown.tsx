import Markdown, { type MarkdownToJSX } from "markdown-to-jsx";
import { type ComponentProps, useMemo } from "react";
import { WorkspaceImage } from "@/components/WorkspaceImage";
import { cn } from "@/lib/utils";

const BASE_OPTIONS = { disableParsingRawHTML: true } satisfies MarkdownToJSX.Options;

/** Options that resolve the images of the document at *documentPath* against the workspace. */
function workspaceOptions(documentPath: string): MarkdownToJSX.Options {
  return {
    ...BASE_OPTIONS,
    overrides: {
      img: {
        component: ({ src, alt }: ComponentProps<"img">) => (
          <WorkspaceImage src={src} alt={alt} documentPath={documentPath} />
        ),
      },
    },
  };
}

interface WorkspaceMarkdownProps {
  children: string;
  /** Containing document path, used to resolve relative image sources. */
  documentPath?: string;
  className?: string;
}

/**
 * Renders a workspace document as markdown, styled by typeset, and, when a document path is given,
 * resolves workspace-relative image sources. The options are memoized so image
 * overrides keep a stable identity and mounted `WorkspaceImage`s do not refetch
 * on every render.
 */
export function WorkspaceMarkdown({ children, documentPath, className }: WorkspaceMarkdownProps) {
  const options = useMemo(
    () => (documentPath ? workspaceOptions(documentPath) : BASE_OPTIONS),
    [documentPath],
  );

  return (
    <div className={cn("typeset min-w-0", className)}>
      <Markdown options={options}>{children}</Markdown>
    </div>
  );
}
