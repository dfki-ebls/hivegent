import {
  ChevronDown,
  FileText,
  FolderOpen,
  Inbox,
  Plus,
  type LucideIcon,
  Upload,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { PERSONAL_SCOPE, formatTarget } from "@/lib/api";
import { cn } from "@/lib/utils";
import { TooltipButton } from "@/components/TooltipButton";
import { Button } from "@/components/ui/button";
import { ButtonGroup, ButtonGroupText } from "@/components/ui/button-group";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface UploadAreaProps {
  isDragging: boolean;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  directoryInputRef: React.RefObject<HTMLInputElement | null>;
  /** Canonical directory uploads and new documents land in. */
  target: string;
  /** Reset the target back to the personal workspace root. */
  onResetTarget: () => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onFileInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onDirectoryInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onSelectFiles: () => void;
  onSelectDirectory: () => void;
  onNewDocument: () => void;
  onNewFolder: () => void;
}

/**
 * A single toolbar button that fans out to a file- and a folder-scoped action,
 * keeping the Upload and Create menus identical in styling and behaviour.
 */
function ActionMenu({
  label,
  icon: Icon,
  onFile,
  onFolder,
}: {
  label: string;
  icon: LucideIcon;
  onFile: () => void;
  onFolder: () => void;
}) {
  const { t } = useTranslation();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="secondary" size="sm" />}>
        <Icon data-icon="inline-start" />
        {label}
        <ChevronDown data-icon="inline-end" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuItem onClick={onFile}>
          <FileText />
          {t(($) => $.documents.upload.file)}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={onFolder}>
          <FolderOpen />
          {t(($) => $.documents.upload.folder)}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// The drop zone stays interactive while uploads are in flight: dropping more
// files appends them to the upload queue (surfaced in the background-task tray)
// rather than replacing or cancelling the work already running.
export function UploadArea({
  isDragging,
  fileInputRef,
  directoryInputRef,
  target,
  onResetTarget,
  onDragOver,
  onDragLeave,
  onDrop,
  onFileInputChange,
  onDirectoryInputChange,
  onSelectFiles,
  onSelectDirectory,
  onNewDocument,
  onNewFolder,
}: UploadAreaProps) {
  const { t } = useTranslation();

  return (
    <div className="border-b p-4">
      <div
        className={cn(
          "flex flex-col items-center gap-3 rounded-2xl border border-dashed p-4 transition-colors",
          isDragging ? "border-primary bg-primary/10" : "bg-muted/25 hover:bg-muted/50",
        )}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          aria-label={t(($) => $.documents.upload.uploadFiles)}
          onChange={onFileInputChange}
        />
        <input
          ref={directoryInputRef}
          type="file"
          webkitdirectory=""
          multiple
          className="hidden"
          aria-label={t(($) => $.documents.upload.uploadFolder)}
          onChange={onDirectoryInputChange}
        />
        {/* A drop-zone icon marks the dashed area as a drop target, deliberately
            not the Upload icon of the menu below. */}
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Inbox className="size-5 shrink-0" />
          {t(($) => $.documents.upload.dropHint)}
        </p>
        <div className="flex flex-wrap justify-center gap-2">
          {/* Upload > File accepts any files, including ZIP archives, which are
              extracted into a collection rather than stored as one document. */}
          <ActionMenu
            label={t(($) => $.common.actions.upload)}
            icon={Upload}
            onFile={onSelectFiles}
            onFolder={onSelectDirectory}
          />
          <ActionMenu
            label={t(($) => $.common.actions.create)}
            icon={Plus}
            onFile={onNewDocument}
            onFolder={onNewFolder}
          />
        </div>
        {/* Where drops, uploads, and new files land, changed by clicking a folder in the tree. */}
        <ButtonGroup className="max-w-full">
          <ButtonGroupText
            className="h-8 min-w-0 bg-background font-normal"
            title={t(($) => $.documents.upload.targetHint)}
          >
            <FolderOpen className="text-muted-foreground" />
            <span className="shrink-0 text-muted-foreground">
              {t(($) => $.documents.upload.workingIn)}
            </span>
            <span className="min-w-0 truncate font-medium">{formatTarget(target)}</span>
          </ButtonGroupText>
          {target !== PERSONAL_SCOPE && (
            <TooltipButton
              variant="outline"
              label={t(($) => $.documents.upload.resetTarget)}
              onClick={onResetTarget}
            >
              <X />
            </TooltipButton>
          )}
        </ButtonGroup>
      </div>
    </div>
  );
}
