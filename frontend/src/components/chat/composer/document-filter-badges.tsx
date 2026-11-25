import { EyeOffIcon, FileTextIcon, FolderIcon, type LucideIcon, XIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { FilterEntry } from "@/api/filters";
import { Badge } from "@/components/ui/badge";
import { InputGroupAddon } from "@/components/ui/input-group";
import { useDocumentFilterStore } from "@/stores/document-filter-store";

interface FilterBadgeProps {
  entry: FilterEntry;
  variant: "secondary" | "destructive";
  icon: LucideIcon;
  onRemove: (path: string) => void;
}

/** One entry of the chat's document filter, which removes itself when pressed. */
function FilterBadge({ entry, variant, icon: Icon, onRemove }: FilterBadgeProps) {
  const { t } = useTranslation();
  const name = entry.path.split("/").pop() ?? entry.path;

  return (
    <Badge
      variant={variant}
      title={entry.path}
      render={
        <button
          type="button"
          aria-label={t(($) => $.chat.composer.removeFilter, { name })}
          onClick={() => onRemove(entry.path)}
        />
      }
    >
      <Icon data-icon="inline-start" />
      {name}
      <XIcon data-icon="inline-end" />
    </Badge>
  );
}

export function DocumentFilterBadges() {
  const included = useDocumentFilterStore((s) => s.included);
  const excluded = useDocumentFilterStore((s) => s.excluded);
  const onRemove = useDocumentFilterStore((s) => s.remove);

  if (included.length === 0 && excluded.length === 0) return null;

  return (
    <InputGroupAddon align="block-start" className="flex-wrap">
      {included.map((entry) => (
        <FilterBadge
          key={`inc-${entry.path}`}
          entry={entry}
          variant="secondary"
          icon={entry.directory ? FolderIcon : FileTextIcon}
          onRemove={onRemove}
        />
      ))}
      {excluded.map((entry) => (
        <FilterBadge
          key={`exc-${entry.path}`}
          entry={entry}
          variant="destructive"
          icon={entry.directory ? FolderIcon : EyeOffIcon}
          onRemove={onRemove}
        />
      ))}
    </InputGroupAddon>
  );
}
