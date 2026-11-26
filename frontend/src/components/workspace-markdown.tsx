import { compiler, type MarkdownToJSX } from "markdown-to-jsx/react";
import { Children, type ComponentProps, Fragment } from "react";
import { WorkspaceImage } from "@/components/workspace-image";
import { useProgressive } from "@/hooks/use-progressive";
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

/** The blocks mounted per frame, grouped so each frame reconciles the groups shown, not their blocks. */
const BLOCKS_PER_FRAME = 200;

/**
 * Renders a workspace document as markdown, styled by typeset, and, when a document path is given,
 * resolves workspace-relative image sources.
 * A long document mounts its blocks progressively and skips laying out those off screen,
 * so opening it never blocks the page.
 */
export function WorkspaceMarkdown({ children, documentPath, className }: WorkspaceMarkdownProps) {
  const options = documentPath ? workspaceOptions(documentPath) : BASE_OPTIONS;
  const blocks = Children.toArray(compiler(children, options));
  const groups = Array.from({ length: Math.ceil(blocks.length / BLOCKS_PER_FRAME) }, (_, i) => (
    <Fragment key={i}>{blocks.slice(i * BLOCKS_PER_FRAME, (i + 1) * BLOCKS_PER_FRAME)}</Fragment>
  ));
  const shown = useProgressive(groups);

  return <div className={cn("typeset lazy-blocks min-w-0", className)}>{shown}</div>;
}
