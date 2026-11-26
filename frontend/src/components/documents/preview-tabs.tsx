import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import { formatDateTime } from "@/i18n/format";
import {
  type DocumentKind,
  type DocumentPreview,
  type MediaPreview,
  type OriginalContent,
  type ParsedPreview,
} from "@/lib/original-preview";
import { documentQueries } from "@/api/documents";
import { AssetMedia } from "@/components/documents/asset-media";
import { LoadingPanel, StatusPanel } from "@/components/common/status-panel";
import { OptionSelect } from "@/components/option-select";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { FileXIcon } from "lucide-react";

export type PreviewTab = "preview" | "text" | "similarity";

/**
 * A document as tabs: its text projection (*children*), its original rendered by the browser,
 * and the *similarity* of a case base, or only the text without either of the others.
 * An image or video stays above the text describing it instead of taking a tab.
 * Only the active tab mounts, so a parsed original loads on demand.
 */
export function PreviewTabs({
  preview,
  similarity,
  tab,
  onTabChange,
  children,
}: {
  preview: DocumentPreview | null;
  similarity?: ReactNode;
  tab: PreviewTab;
  onTabChange: (tab: PreviewTab) => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const original = preview && preview.kind !== "media" ? preview : null;
  const text =
    preview?.kind === "media" ? <MediaAbove media={preview}>{children}</MediaAbove> : children;

  if (!original && !similarity) return text;

  const shown =
    (tab === "preview" && !original) || (tab === "similarity" && !similarity) ? "text" : tab;

  return (
    <Tabs value={shown} onValueChange={onTabChange} className="min-h-0 flex-1 gap-0">
      <div className="border-b px-4 py-2">
        <TabsList>
          <TabsTrigger value="text">{t(($) => $.documents.preview.textTab)}</TabsTrigger>
          {original && (
            <TabsTrigger value="preview">{t(($) => $.documents.preview.previewTab)}</TabsTrigger>
          )}
          {similarity && (
            <TabsTrigger value="similarity">{t(($) => $.documents.similarity.tab)}</TabsTrigger>
          )}
        </TabsList>
      </div>
      <TabsContent value="text" className="flex min-h-0 flex-col">
        {text}
      </TabsContent>
      {original && (
        <TabsContent value="preview" className="flex min-h-0 flex-col">
          <OriginalPreview preview={original} />
        </TabsContent>
      )}
      {similarity && (
        <TabsContent value="similarity" className="flex min-h-0 flex-col">
          {similarity}
        </TabsContent>
      )}
    </Tabs>
  );
}

/** *media* pinned above the text describing it (*children*), which scrolls on its own. */
function MediaAbove({ media, children }: { media: MediaPreview; children: ReactNode }) {
  return (
    <>
      <div className="flex shrink-0 justify-center px-4 pt-4">
        <AssetMedia
          key={media.path}
          filePath={media.path}
          mediaType={media.mediaType}
          alt={media.alt}
          wrapperClassName="min-h-32 min-w-32 rounded-xl border"
          className="max-h-[40dvh] w-auto max-w-full object-contain"
        />
      </div>
      {children}
    </>
  );
}

function OriginalPreview({ preview }: { preview: ParsedPreview }) {
  switch (preview.kind) {
    case "pdf":
      return (
        <ParsedOriginal path={preview.documentPath} kind="pdf">
          {(url) => <PdfFrame url={url} title={preview.documentPath} />}
        </ParsedOriginal>
      );
    case "docx":
      return (
        <ParsedOriginal path={preview.documentPath} kind="docx">
          {(html) => (
            <ScrollArea className="min-h-0 flex-1">
              {/* Sanitized by DOMPurify when parsed. */}
              <div className="typeset min-w-0 p-4" dangerouslySetInnerHTML={{ __html: html }} />
            </ScrollArea>
          )}
        </ParsedOriginal>
      );
    case "xlsx":
      return (
        <ParsedOriginal path={preview.documentPath} kind="xlsx">
          {(sheets) => <SheetTable sheets={sheets} />}
        </ParsedOriginal>
      );
  }
}

/** The original of *path* parsed for *kind*, with its loading and failure states. */
function ParsedOriginal<K extends DocumentKind>({
  path,
  kind,
  children,
}: {
  path: string;
  kind: K;
  children: (content: OriginalContent[K]) => ReactNode;
}) {
  const { t } = useTranslation();
  const { data, error } = useQuery(documentQueries.original(path, kind));

  if (error) {
    return <StatusPanel icon={FileXIcon} title={t(($) => $.documents.preview.unavailable)} />;
  }

  return data === undefined ? <LoadingPanel /> : children(data);
}

/** The browser's own PDF viewer, unsandboxed since Chromium refuses PDFs in sandboxed frames. */
function PdfFrame({ url, title }: { url: string; title: string }) {
  return <iframe src={url} title={title} className="min-h-0 flex-1" />;
}

const MAX_ROWS = 500;

/** A read-only table of one sheet at a time, showing its first rows. */
function SheetTable({ sheets }: { sheets: OriginalContent["xlsx"] }) {
  const { t } = useTranslation();
  const [index, setIndex] = useState(0);
  const rows = sheets.at(index)?.data ?? [];

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 p-4">
      {sheets.length > 1 && (
        <OptionSelect
          size="sm"
          aria-label={t(($) => $.documents.preview.sheet)}
          value={String(index)}
          options={sheets.map(({ sheet }, i) => ({ value: String(i), label: sheet }))}
          onChange={(value) => setIndex(Number(value))}
        />
      )}
      {rows.length > MAX_ROWS && (
        <p className="text-xs text-muted-foreground">
          {t(($) => $.documents.preview.truncated, { rows: MAX_ROWS })}
        </p>
      )}
      <ScrollArea className="min-h-0 flex-1 rounded-md border">
        <Table className="text-xs">
          <TableBody>
            {rows.slice(0, MAX_ROWS).map((row, r) => (
              <TableRow key={r}>
                {row.map((cell, c) => (
                  <TableCell key={c}>
                    {cell instanceof Date ? formatDateTime(cell) : String(cell ?? "")}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </ScrollArea>
    </div>
  );
}
