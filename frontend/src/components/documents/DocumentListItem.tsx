import { FileText, RotateCcw, Scissors, Trash2 } from "lucide-react";

import type { DocumentInfo } from "../../lib/types";
import { formatFileSize } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Spinner } from "../ui/spinner";
import { FilterToggleButtons, type FilterEntryState } from "./FilterToggleButtons";
import { formatRelativeDate } from "./utils";

interface DocumentListItemProps {
  doc: DocumentInfo;
  isMutating: boolean;
  onEdit: () => void;
  filterState: FilterEntryState;
  onIncludeDocument: () => void;
  onExcludeDocument: () => void;
  onReconvert: () => void;
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
  onReconvert,
  onRemove,
  selected,
  onToggleSelect,
}: DocumentListItemProps) {
  return (
    <button
      type="button"
      className="flex w-full items-center gap-3 rounded-lg border bg-card p-3 transition-colors hover:bg-muted/50 cursor-pointer text-left"
      onClick={onEdit}
    >
      {onToggleSelect && (
        <Checkbox
          checked={selected ?? false}
          onCheckedChange={() => onToggleSelect()}
          onClick={(e) => e.stopPropagation()}
          className="shrink-0"
        />
      )}
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
      {doc.has_original && (
        <Button
          variant="ghost"
          size="icon"
          title="Reconvert from original"
          onClick={(e) => {
            e.stopPropagation();
            onReconvert();
          }}
          disabled={isMutating}
        >
          <RotateCcw className="h-4 w-4" />
        </Button>
      )}
      <FilterToggleButtons
        state={filterState}
        onInclude={onIncludeDocument}
        onExclude={onExcludeDocument}
      />
      <Button
        variant="ghost"
        size="icon"
        title="Remove"
        onClick={(e) => {
          e.stopPropagation();
          onRemove();
        }}
        disabled={isMutating}
      >
        <Trash2 className="h-4 w-4 text-destructive" />
      </Button>
    </button>
  );
}
