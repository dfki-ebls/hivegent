import { X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { DOCUMENT_ACTIONS, type DocumentActionId } from "@/lib/document-actions";
import { TooltipButton } from "@/components/TooltipButton";
import { Button } from "@/components/ui/button";

interface BulkActionBarProps {
  selectedCount: number;
  /** Whether any selected file has an original (gates the reconvert/download actions). */
  hasReconvertable: boolean;
  handlers: Record<DocumentActionId, () => void>;
  onClear: () => void;
}

/**
 * Selection summary plus bulk actions. Rechunk and reconvert submit a background
 * job whose progress shows in the job tray, while delete applies to the whole
 * selection at once and shows its spinners and any error in the tree.
 */
export function BulkActionBar({
  selectedCount,
  hasReconvertable,
  handlers,
  onClear,
}: BulkActionBarProps) {
  const { t } = useTranslation();

  return (
    <div className="flex min-h-9 flex-wrap items-center gap-2 px-1">
      <TooltipButton
        size="icon-xs"
        label={t(($) => $.documents.scope.clearSelection)}
        onClick={onClear}
      >
        <X />
      </TooltipButton>
      <span className="text-sm font-medium">
        {t(($) => $.documents.scope.selected, { count: selectedCount })}
      </span>
      {DOCUMENT_ACTIONS.filter((action) => hasReconvertable || !action.requiresOriginal).map(
        ({ id, icon: Icon, variant }) => (
          <Button key={id} variant={variant} size="sm" onClick={handlers[id]}>
            <Icon data-icon="inline-start" />
            {t(($) => $.documents.actions[id])}
          </Button>
        ),
      )}
    </div>
  );
}
