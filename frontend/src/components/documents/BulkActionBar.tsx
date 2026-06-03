import { Loader2, X } from "lucide-react";

import { DOCUMENT_ACTIONS } from "../../lib/document-actions";
import type { UploadProgress } from "../../lib/types";
import { Button } from "../ui/button";
import { Progress } from "../ui/progress";

interface BulkActionBarProps {
  bulkProgress: UploadProgress | null;
  selectedCount: number;
  /** Whether any selected file has an original (gates the reconvert/download actions). */
  hasReconvertable: boolean;
  handlers: Record<string, () => void>;
  onClear: () => void;
}

/** Selection summary plus bulk actions, or a progress bar while a bulk op runs. */
export function BulkActionBar({
  bulkProgress,
  selectedCount,
  hasReconvertable,
  handlers,
  onClear,
}: BulkActionBarProps) {
  return (
    <div className="flex h-9 items-center gap-2 py-1">
      {bulkProgress ? (
        <>
          <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
          <span className="truncate text-sm">{bulkProgress.currentFile}</span>
          <Progress
            value={bulkProgress.total > 0 ? (bulkProgress.current / bulkProgress.total) * 100 : 0}
            className="w-24 shrink-0"
          />
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {bulkProgress.current}/{bulkProgress.total}
          </span>
        </>
      ) : (
        <>
          <span className="text-sm font-medium">{selectedCount} selected</span>
          {DOCUMENT_ACTIONS.map((action) => {
            if (action.requiresOriginal && !hasReconvertable) return null;
            const Icon = action.icon;
            return (
              <Button
                key={action.id}
                variant={action.variant}
                size="sm"
                onClick={handlers[action.id]}
              >
                <Icon className="h-4 w-4 mr-1" />
                {action.label}
              </Button>
            );
          })}
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onClear}>
            <X className="h-4 w-4" />
          </Button>
        </>
      )}
    </div>
  );
}
