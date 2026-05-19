import { EyeOff, FileText, Folder, type LucideIcon, X } from "lucide-react";
import { PromptInputHeader } from "@/components/ai-elements/prompt-input";
import { Badge } from "@/components/ui/badge";

interface DocumentFilterBadgesProps {
  included: string[];
  excluded: string[];
  onRemove: (filename: string) => void;
}

function entryDisplayName(entry: string): string {
  const isDir = entry.endsWith("/");
  if (isDir) return entry.slice(0, -1).split("/").pop() ?? entry;
  return entry.split("/").pop() ?? entry;
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

export function DocumentFilterBadges({ included, excluded, onRemove }: DocumentFilterBadgesProps) {
  if (included.length === 0 && excluded.length === 0) return null;

  return (
    <PromptInputHeader>
      {included.map((entry) => (
        <FilterBadge
          key={`inc-${entry}`}
          entry={entry}
          variant="secondary"
          icon={entry.endsWith("/") ? Folder : FileText}
          onRemove={onRemove}
        />
      ))}
      {excluded.map((entry) => (
        <FilterBadge
          key={`exc-${entry}`}
          entry={entry}
          variant="destructive"
          icon={entry.endsWith("/") ? Folder : EyeOff}
          onRemove={onRemove}
        />
      ))}
    </PromptInputHeader>
  );
}
