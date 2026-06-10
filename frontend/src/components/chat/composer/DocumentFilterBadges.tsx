import { EyeOff, FileText, Folder, type LucideIcon, X } from "lucide-react";
import { PromptInputHeader } from "@/components/ai-elements/prompt-input";
import { Badge } from "@/components/ui/badge";
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

function FilterBadge({ entry, variant, icon: Icon, onRemove }: FilterBadgeProps) {
  return (
    <Badge variant={variant} className="gap-1 text-xs" title={entry}>
      <Icon className="h-3 w-3" />
      {entryDisplayName(entry)}
      <button
        type="button"
        className="ml-0.5 rounded-full hover:bg-muted"
        onClick={() => onRemove(entry)}
      >
        <X className="h-3 w-3" />
      </button>
    </Badge>
  );
}

export function DocumentFilterBadges() {
  const included = useDocumentFilterStore((s) => s.included);
  const excluded = useDocumentFilterStore((s) => s.excluded);
  const onRemove = useDocumentFilterStore((s) => s.remove);
  if (included.length === 0 && excluded.length === 0) return null;

  return (
    <PromptInputHeader>
      {included.map((entry) => (
        <FilterBadge
          key={`inc-${entry}`}
          entry={entry}
          variant="secondary"
          icon={isDirEntry(entry) ? Folder : FileText}
          onRemove={onRemove}
        />
      ))}
      {excluded.map((entry) => (
        <FilterBadge
          key={`exc-${entry}`}
          entry={entry}
          variant="destructive"
          icon={isDirEntry(entry) ? Folder : EyeOff}
          onRemove={onRemove}
        />
      ))}
    </PromptInputHeader>
  );
}
