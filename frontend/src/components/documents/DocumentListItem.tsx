import { FileText, Scissors, Trash2 } from "lucide-react";

import type { DocumentInfo } from "@/lib/types";
import { formatFileSize } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Spinner } from "@/components/ui/spinner";
import {
  FilterToggleButtons,
  type FilterEntryState,
} from "@/components/documents/FilterToggleButtons";
import { formatRelativeDate } from "@/components/documents/utils";

interface DocumentListItemProps {
  doc: DocumentInfo;
  isMutating: boolean;
  onEdit: () => void;
  filterState: FilterEntryState;
  onIncludeDocument: () => void;
  onExcludeDocument: () => void;
  onRemove: () => void;
  selected?: boolean;
  onToggleSelect?: () => void;
}

export function DocumentListItem({
  doc,
  isMutating,
  onEdit,
  filterState,
  onIncludeDocument,
  onExcludeDocument,
  onRemove,
  selected,
  onToggleSelect,
}: DocumentListItemProps) {
  return (
    <div className="flex w-full items-center gap-3 rounded-lg border bg-card p-3 transition-colors hover:bg-muted/50">
      {onToggleSelect && (
        <Checkbox
          checked={selected ?? false}
          onCheckedChange={() => onToggleSelect()}
          className="shrink-0"
        />
      )}
      <button
        type="button"
        className="flex min-w-0 flex-1 items-center gap-3 text-left cursor-pointer"
        onClick={onEdit}
      >
        <FileText className="h-8 w-8 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate font-medium text-sm">{doc.display_name}</p>
            {isMutating && <Spinner className="size-3 shrink-0 text-muted-foreground" />}
            {doc.chunk_count != null && (
              <Badge variant="outline" className="shrink-0 text-xs gap-1">
                <Scissors className="h-3 w-3" />
                {doc.chunk_count}
              </Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {formatFileSize(doc.size_bytes)} · {formatRelativeDate(doc.modified_at)}
          </p>
        </div>
      </button>
      <FilterToggleButtons
        state={filterState}
        onInclude={onIncludeDocument}
        onExclude={onExcludeDocument}
      />
      <Button variant="ghost" size="icon" title="Remove" onClick={onRemove} disabled={isMutating}>
        <Trash2 className="h-4 w-4 text-destructive" />
      </Button>
    </div>
  );
}
