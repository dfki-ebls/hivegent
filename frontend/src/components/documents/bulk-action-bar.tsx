import { X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { DOCUMENT_ACTIONS, type DocumentActionId } from "@/lib/document-actions";
import type { DocumentTree } from "@/hooks/use-document-tree";
import { useFileActions } from "@/hooks/use-file-actions";
import { TooltipButton } from "@/components/common/tooltip-button";

interface BulkActionBarProps {
  docTree: DocumentTree;
}

/**
 * The selection summary and its actions, one row as tall as the search it
 * stands in for, so selecting never shifts the tree. The action labels show
 * once the row is wide enough, the icons and tooltips always. Rechunk and
 * reconvert submit a job whose progress shows in the job tray, while delete
 * asks first and shows its spinners and any error in the tree.
 */
export function BulkActionBar({ docTree }: BulkActionBarProps) {
  const { t } = useTranslation();
  const { selectedItems, selectedEntries, selectedFiles, clearSelection } = docTree;
  const actions = useFileActions();
  const originals = selectedFiles.filter((file) => file.entry.has_original === true);
  const writable = selectedEntries.every((entry) => entry.writable);

  // Each action with the number of selected items it applies to, hidden at none.
  const handlers: Record<DocumentActionId, { targets: number; run: () => void }> = {
    rechunk: {
      targets: selectedFiles.length,
      run: () => {
        clearSelection();
        actions.rechunk(selectedFiles);
      },
    },
    reconvert: {
      targets: originals.length,
      run: () => {
        clearSelection();
        actions.reconvert(originals);
      },
    },
    download: { targets: originals.length, run: () => actions.download(originals) },
    delete: {
      targets: selectedEntries.length,
      run: () => docTree.deleteDialog.show(selectedEntries),
    },
  };

  return (
    <div className="@container min-w-0 flex-1">
      <div className="flex h-9 items-center gap-1">
        <TooltipButton
          size="icon-xs"
          label={t(($) => $.documents.selection.clearSelection)}
          onClick={clearSelection}
        >
          <X />
        </TooltipButton>
        <span className="mr-auto truncate text-sm font-medium">
          {t(($) => $.documents.selection.selected, { count: selectedItems.length })}
        </span>
        {DOCUMENT_ACTIONS.filter(
          (action) => handlers[action.id].targets > 0 && (writable || !action.requiresWrite),
        ).map(({ id, icon: Icon, variant }) => (
          <TooltipButton
            key={id}
            variant={variant}
            size="sm"
            label={t(($) => $.documents.actions[id])}
            onClick={handlers[id].run}
          >
            <Icon data-icon="inline-start" />
            <span className="hidden @md:inline">{t(($) => $.documents.actions[id])}</span>
          </TooltipButton>
        ))}
      </div>
    </div>
  );
}
