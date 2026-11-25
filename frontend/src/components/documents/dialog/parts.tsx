import { type ComponentProps, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { formatFileSize } from "@/i18n/format";
import { cn } from "@/lib/utils";
import { type ContentChunk } from "@/components/documents/dialog/model";
import { ItemButton } from "@/components/row-buttons";
import { WorkspaceMarkdown } from "@/components/workspace-markdown";
import { Badge } from "@/components/ui/badge";
import { CodeBlock, CodeBlockContent } from "@/components/ui/code-block";
import { ScrollArea } from "@/components/ui/scroll-area";

/** Read-only markdown description with an empty-state fallback. */
export function DescriptionBody({ markdown }: { markdown: string | null }) {
  const { t } = useTranslation();

  return markdown ? (
    <WorkspaceMarkdown>{markdown}</WorkspaceMarkdown>
  ) : (
    <p className="text-sm text-muted-foreground italic">
      {t(($) => $.documents.dialog.noDescription)}
    </p>
  );
}

/** Outline badges describing a file's name, media type, and size. */
export function FileMetaBadges({
  name,
  mediaType,
  sizeBytes,
}: {
  name: string;
  mediaType?: string | null;
  sizeBytes?: number | null;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      <Badge variant="outline">{name}</Badge>
      {mediaType && <Badge variant="outline">{mediaType}</Badge>}
      {sizeBytes != null && <Badge variant="outline">{formatFileSize(sizeBytes)}</Badge>}
    </div>
  );
}

/** The origin and position badges of a fetched chunk, where a long search query wraps. */
export function ChunkBadges({ chunk }: { chunk: ContentChunk }) {
  return (
    <>
      <Badge variant="outline" className="h-auto justify-start whitespace-normal wrap-anywhere">
        {chunk.originLabel}
      </Badge>
      <Badge variant="secondary">{chunk.badge}</Badge>
    </>
  );
}

/** A scrolling pane with the description of an image or video and its file metadata. */
export function DescriptionPane({ children }: { children: ReactNode }) {
  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="flex flex-col gap-2 p-4">{children}</div>
    </ScrollArea>
  );
}

/** The document text as one wrapped, scrolling code block filling the pane, the *span* holding it. */
export function TextPane(props: ComponentProps<"span">) {
  return (
    <div className="flex min-h-0 flex-1 flex-col p-4">
      <CodeBlock className="min-h-0 flex-1">
        <CodeBlockContent wrap>
          <span {...props} />
        </CodeBlockContent>
      </CodeBlock>
    </div>
  );
}

/** A selectable sidebar entry, highlighted while its view is shown. */
export function SidebarItem({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <ItemButton
      size="xs"
      aria-current={active}
      onClick={onClick}
      className={cn(active && "bg-muted")}
    >
      {children}
    </ItemButton>
  );
}

/** Media views (an uploaded image or video, an extracted asset) title their description alike. */
export function DescriptionHeading({ actions }: { actions?: ReactNode }) {
  const { t } = useTranslation();

  return (
    <div className="flex items-center justify-between gap-2">
      <h3 className="text-sm font-medium">{t(($) => $.documents.dialog.descriptionHeading)}</h3>
      {actions}
    </div>
  );
}
