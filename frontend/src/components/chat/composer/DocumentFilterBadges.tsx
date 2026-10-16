import { EyeOffIcon, FileTextIcon, FolderIcon, type LucideIcon, XIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { InputGroupAddon } from "@/components/ui/input-group";
import { useDocumentFilterStore } from "@/stores/document-filter-store";

/** Directory entries end with `/`; a bare scope (`~`, `@team`) is a whole workspace. */
function isDirEntry(entry: string): boolean {
  return entry.endsWith("/") || !entry.includes("/");
}

function entryDisplayName(entry: string): string {
  return entry.replace(/\/$/, "").split("/").pop() ?? entry;
}

interface FilterBadgeProps {
  entry: string;
  variant: "secondary" | "destructive";
  icon: LucideIcon;
  onRemove: (filename: string) => void;
}

/** One entry of the chat's document filter, which removes itself when pressed. */
function FilterBadge({ entry, variant, icon: Icon, onRemove }: FilterBadgeProps) {
  const { t } = useTranslation();
  const name = entryDisplayName(entry);

  return (
    <Badge
      variant={variant}
      title={entry}
      render={
        <button
          type="button"
          aria-label={t(($) => $.chat.composer.removeFilter, { name })}
          onClick={() => onRemove(entry)}
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
          key={`inc-${entry}`}
          entry={entry}
          variant="secondary"
          icon={isDirEntry(entry) ? FolderIcon : FileTextIcon}
          onRemove={onRemove}
        />
      ))}
      {excluded.map((entry) => (
        <FilterBadge
          key={`exc-${entry}`}
          entry={entry}
          variant="destructive"
          icon={isDirEntry(entry) ? FolderIcon : EyeOffIcon}
          onRemove={onRemove}
        />
      ))}
    </InputGroupAddon>
  );
}
