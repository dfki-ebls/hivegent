import { FileText, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { DocumentInfo } from "@/lib/types";
import { formatFileSize, formatRelativeTime } from "@/i18n/format";
import { TooltipButton } from "@/components/TooltipButton";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item";
import { Spinner } from "@/components/ui/spinner";
import {
  FilterToggleButtons,
  type FilterEntryState,
} from "@/components/documents/FilterToggleButtons";
import { ChunkCountBadge, NameButton } from "@/components/RowButtons";

interface DocumentListItemProps {
  doc: DocumentInfo;
  isMutating: boolean;
  onEdit: () => void;
  filterState: FilterEntryState;
  onIncludeDocument: () => void;
  onExcludeDocument: () => void;
  /** Delete the document, omitted in a read-only scope. */
  onRemove?: () => void;
  selected?: boolean;
  onToggleSelect?: () => void;
}

/** A search result row: the document, its metadata, and its filter and delete actions. */
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
  const { t } = useTranslation();

  return (
    <Item variant="outline" size="sm" className="flex-nowrap">
      {onToggleSelect && (
        <Checkbox
          checked={selected ?? false}
          onCheckedChange={onToggleSelect}
          aria-label={doc.display_name}
        />
      )}
      <ItemMedia variant="icon">
        <FileText className="text-muted-foreground" />
      </ItemMedia>
      <ItemContent className="min-w-0">
        <ItemTitle className="max-w-full">
          <NameButton onClick={onEdit}>{doc.display_name}</NameButton>
          {isMutating && (
            <Spinner
              aria-label={t(($) => $.common.states.working)}
              className="text-muted-foreground"
            />
          )}
          {doc.chunk_count != null && <ChunkCountBadge count={doc.chunk_count} />}
        </ItemTitle>
        <ItemDescription>
          {formatFileSize(doc.size_bytes)} · {formatRelativeTime(doc.modified_at)}
        </ItemDescription>
      </ItemContent>
      <ItemActions className="gap-0.5">
        <FilterToggleButtons
          state={filterState}
          onInclude={onIncludeDocument}
          onExclude={onExcludeDocument}
        />
        {onRemove && (
          <TooltipButton
            size="icon-xs"
            destructive
            label={t(($) => $.common.actions.remove)}
            onClick={onRemove}
            disabled={isMutating}
          >
            <Trash2 />
          </TooltipButton>
        )}
      </ItemActions>
    </Item>
  );
}
