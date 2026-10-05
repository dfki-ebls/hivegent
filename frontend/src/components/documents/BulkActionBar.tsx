import { X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { DOCUMENT_ACTIONS } from "@/lib/document-actions";
import { Button } from "@/components/ui/button";

interface BulkActionBarProps {
  selectedCount: number;
  /** Whether any selected file has an original (gates the reconvert/download actions). */
  hasReconvertable: boolean;
  handlers: Record<string, () => void>;
  onClear: () => void;
}

/**
 * Selection summary plus bulk actions. Each action submits a background job, so
 * its progress shows in the job tray rather than inline here.
 */
export function BulkActionBar({
  selectedCount,
  hasReconvertable,
  handlers,
  onClear,
}: BulkActionBarProps) {
  const { t } = useTranslation();

  return (
    <div className="flex h-9 items-center gap-2 px-2 py-1">
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7 -mx-1.5"
        aria-label={t(($) => $.documents.scope.clearSelection)}
        onClick={onClear}
      >
        <X className="h-4 w-4" />
      </Button>
      <span className="text-sm font-medium">
        {t(($) => $.documents.scope.selected, { count: selectedCount })}
      </span>
      {DOCUMENT_ACTIONS.map((action) => {
        if (action.requiresOriginal && !hasReconvertable) return null;
        const Icon = action.icon;
        return (
          <Button key={action.id} variant={action.variant} size="sm" onClick={handlers[action.id]}>
            <Icon className="h-4 w-4 mr-1" />
            {t(($) => $.documents.actions[action.id])}
          </Button>
        );
      })}
    </div>
  );
}
