import Markdown, { type MarkdownToJSX } from "markdown-to-jsx";
import type { ComponentProps } from "react";
import { WorkspaceImage } from "@/components/workspace-image";
import { cn } from "@/lib/utils";

/** A link of a document, opened in a new tab so the app stays, unless it points within the page. */
function WorkspaceLink({ children, ...props }: ComponentProps<"a">) {
  const inPage = props.href?.startsWith("#") ?? false;

  return (
    <a
      {...props}
      target={inPage ? undefined : "_blank"}
      rel={inPage ? undefined : "noopener noreferrer"}
    >
      {children}
    </a>
  );
}

// The blocks sit right in the typeset container, unwrapped.
const BASE_OPTIONS = {
  disableParsingRawHTML: true,
  wrapper: null,
  overrides: { a: { component: WorkspaceLink } },
} satisfies MarkdownToJSX.Options;

/** Options that resolve the images of the document at *documentPath* against the workspace. */
function workspaceOptions(documentPath: string): MarkdownToJSX.Options {
  return {
    ...BASE_OPTIONS,
    overrides: {
      ...BASE_OPTIONS.overrides,
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
 * resolves workspace-relative image sources.
 */
export function WorkspaceMarkdown({ children, documentPath, className }: WorkspaceMarkdownProps) {
  const options = documentPath ? workspaceOptions(documentPath) : BASE_OPTIONS;

  return (
    <div className={cn("typeset min-w-0", className)}>
      <Markdown options={options}>{children}</Markdown>
    </div>
  );
}
