import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { useObjectUrl } from "@/hooks/use-object-url";
import { formatDateTime } from "@/i18n/format";
import {
  type DocumentKind,
  type DocumentPreview,
  type OriginalContent,
} from "@/lib/original-preview";
import { documentQueries } from "@/api/documents";
import { AssetMedia } from "@/components/documents/asset-media";
import { LoadingPanel, StatusPanel } from "@/components/common/status-panel";
import { OptionSelect } from "@/components/option-select";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { FileXIcon } from "lucide-react";

export type PreviewTab = "preview" | "text";

/**
 * A document as two tabs, its original rendered by the browser and its text projection (*children*),
 * or only the text without a *preview*. Only the active tab mounts, so the original loads on demand.
 */
export function PreviewTabs({
  preview,
  tab,
  onTabChange,
  children,
}: {
  preview: DocumentPreview | null;
  tab: PreviewTab;
  onTabChange: (tab: PreviewTab) => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();

  if (!preview) return children;

  return (
    <Tabs value={tab} onValueChange={onTabChange} className="min-h-0 flex-1 gap-0">
      <div className="border-b px-4 py-2">
        <TabsList>
          <TabsTrigger value="preview">{t(($) => $.documents.preview.previewTab)}</TabsTrigger>
          <TabsTrigger value="text">{t(($) => $.documents.preview.textTab)}</TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="preview" className="flex min-h-0 flex-col">
        <OriginalPreview preview={preview} />
      </TabsContent>
      <TabsContent value="text" className="flex min-h-0 flex-col">
        {children}
      </TabsContent>
    </Tabs>
  );
}

function OriginalPreview({ preview }: { preview: DocumentPreview }) {
  switch (preview.kind) {
    case "media":
      return (
        <ScrollArea className="min-h-0 flex-1">
          <div className="flex justify-center p-4">
            <AssetMedia
              key={preview.path}
              filePath={preview.path}
              mediaType={preview.mediaType}
              alt={preview.alt}
              wrapperClassName="min-h-32 min-w-32 rounded-xl border"
              className="max-h-[70dvh] w-auto max-w-full object-contain"
            />
          </div>
        </ScrollArea>
      );
    case "pdf":
      return (
        <ParsedOriginal path={preview.documentPath} kind="pdf">
          {(blob) => <PdfFrame blob={blob} title={preview.documentPath} />}
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
function PdfFrame({ blob, title }: { blob: Blob; title: string }) {
  const fetch = useCallback(async () => URL.createObjectURL(blob), [blob]);
  const { url } = useObjectUrl(fetch);

  return url ? <iframe src={url} title={title} className="min-h-0 flex-1" /> : <LoadingPanel />;
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
