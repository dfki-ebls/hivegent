import Markdown from "markdown-to-jsx";
import { useMemo } from "react";

import { MARKDOWN_BASE_OPTIONS, workspaceMarkdownOptions } from "@/components/chat/markdown/config";
import { cn } from "@/lib/utils";

interface WorkspaceMarkdownProps {
  children: string;
  /** Containing document path, used to resolve relative image sources. */
  documentPath?: string;
  className?: string;
}

/**
 * Renders a workspace document as markdown and, when a document path is given,
 * resolves workspace-relative image sources. The options are memoized so image
 * overrides keep a stable identity and mounted `WorkspaceImage`s do not refetch
 * on every render.
 */
export function WorkspaceMarkdown({ children, documentPath, className }: WorkspaceMarkdownProps) {
  const options = useMemo(
    () => (documentPath ? workspaceMarkdownOptions(documentPath) : MARKDOWN_BASE_OPTIONS),
    [documentPath],
  );

  return (
    <div className={cn("prose prose-sm dark:prose-invert max-w-none", className)}>
      <Markdown options={options}>{children}</Markdown>
    </div>
  );
}
