import { Download, ExternalLink, Pencil, RefreshCw, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { formatDateTime, formatFileSize } from "@/i18n/format";
import { downloadBlob } from "@/lib/download";
import { basename } from "@/lib/utils";
import { type ContentModel } from "@/components/documents/dialog/model";
import { PendingIcon } from "@/components/common/spinner";
import { TooltipButton } from "@/components/common/tooltip-button";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

/** The title, metadata badges, and document actions of the dialog. */
export function DocumentHeader({
  filename,
  isWeb,
  isManagedMode,
  showMetadata,
  editable,
  isEditing,
  contentModel,
  rechunking,
  onEdit,
  onRechunk,
  onReconvert,
  onDownloadOriginal,
}: {
  filename: string;
  isWeb: boolean;
  isManagedMode: boolean;
  showMetadata: boolean;
  editable: boolean;
  isEditing: boolean;
  contentModel: ContentModel;
  rechunking: boolean;
  onEdit: () => void;
  onRechunk?: () => void;
  onReconvert?: () => void;
  onDownloadOriginal?: () => void;
}) {
  const { t } = useTranslation();
  const fullContent = contentModel.content;

  // Download the markdown description shown in the dialog under its on-disk name.
  const handleDownloadMarkdown = () => {
    if (fullContent == null) return;
    const name = basename(filename) || "document.md";
    downloadBlob(new Blob([fullContent], { type: "text/markdown" }), name);
  };

  return (
    <DialogHeader className="px-6 pt-6 pb-4">
      <DialogTitle className="flex min-w-0 items-center gap-2 pr-8 leading-normal">
        <span className="truncate">{filename}</span>
        {isWeb && (
          <TooltipButton
            size="icon-xs"
            label={t(($) => $.documents.dialog.openInBrowser)}
            nativeButton={false}
            // oxlint-disable-next-line jsx-a11y/control-has-associated-label -- the button renders its label into the link
            render={<a href={filename} target="_blank" rel="noreferrer" />}
          >
            <ExternalLink />
          </TooltipButton>
        )}
      </DialogTitle>
      <DialogDescription className="sr-only">
        {t(($) => $.documents.dialog.description, { filename })}
      </DialogDescription>

      {/* Metadata badges, then the document actions on their own line below. */}
      {(showMetadata || editable) && (
        <div className="flex flex-col gap-2 pt-1">
          {showMetadata && contentModel.metadata && (
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary">
                {t(($) => $.documents.dialog.chunking, {
                  pipeline: contentModel.metadata.pipeline,
                })}
              </Badge>
              <Badge variant="secondary">
                {t(($) => $.documents.chunks, { count: contentModel.chunks.length })}
              </Badge>
              {contentModel.metadata.size_bytes != null && (
                <Badge variant="outline">{formatFileSize(contentModel.metadata.size_bytes)}</Badge>
              )}
              <Badge variant="outline">
                {t(($) => $.documents.dialog.created, {
                  date: formatDateTime(contentModel.metadata.created_at),
                })}
              </Badge>
            </div>
          )}
          {!isEditing && (
            <div className="flex flex-wrap items-center gap-2">
              {editable && (
                <Button variant="outline" size="sm" onClick={onEdit}>
                  <Pencil data-icon="inline-start" />
                  {t(($) => $.common.actions.edit)}
                </Button>
              )}
              {onRechunk && (
                <Button variant="outline" size="sm" onClick={onRechunk} disabled={rechunking}>
                  <PendingIcon pending={rechunking} icon={RefreshCw} />
                  {t(($) => $.documents.actions.rechunk)}
                </Button>
              )}
              {onReconvert && (
                <Button variant="outline" size="sm" onClick={onReconvert}>
                  <RotateCcw data-icon="inline-start" />
                  {t(($) => $.documents.actions.reconvert)}
                </Button>
              )}
              {onDownloadOriginal && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={onDownloadOriginal}
                  title={t(($) => $.documents.dialog.downloadOriginal)}
                >
                  <Download data-icon="inline-start" />
                  {t(($) => $.documents.dialog.original)}
                </Button>
              )}
              {isManagedMode && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleDownloadMarkdown}
                  disabled={fullContent == null}
                  title={t(($) => $.documents.dialog.downloadMarkdown)}
                >
                  <Download data-icon="inline-start" />
                  {t(($) => $.documents.dialog.markdown)}
                </Button>
              )}
            </div>
          )}
        </div>
      )}
    </DialogHeader>
  );
}
