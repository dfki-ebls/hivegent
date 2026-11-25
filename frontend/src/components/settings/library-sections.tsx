import { type UseMutationOptions, useMutation } from "@tanstack/react-query";
import { TrashIcon } from "lucide-react";
import type { ReactNode } from "react";

import { WithTooltip } from "@/components/common/tooltip-button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Section } from "@/components/settings/section";
import { Button } from "@/components/ui/button";
import { useDialogTarget } from "@/hooks/use-dialog-target";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { cn } from "@/lib/utils";

/** An entry of a scoped library, such as a skill or an assistant. */
interface LibraryEntry {
  name: string;
  scope: string;
  writable: boolean;
}

interface LibrarySectionsProps<T extends LibraryEntry> {
  entries: readonly T[];
  /** Shows only the global scope, else every scope with entries or a library the user may change. */
  global?: boolean;
  remove: UseMutationOptions<void, Error, T>;
  labels: {
    empty: string;
    delete: string;
    deleteTitle: (entry: T) => string;
    deleteDescription: string;
  };
  listClassName?: string;
  entryKey: (entry: T) => string;
  /** What an entry shows next to its actions. */
  renderEntry: (entry: T) => ReactNode;
  /** The actions of a writable entry before its delete button. */
  renderActions?: (entry: T) => ReactNode;
  /** What a scope offers below its entries when the user may change its library. */
  renderLibrary: (scope: string) => ReactNode;
}

/** The entries of every scope the user sees, one card each, deleted behind one confirmation. */
export function LibrarySections<T extends LibraryEntry>({
  entries,
  global = false,
  remove,
  labels,
  listClassName,
  entryKey,
  renderEntry,
  renderActions,
  renderLibrary,
}: LibrarySectionsProps<T>) {
  const workspaces = useWorkspaces();
  const deletion = useDialogTarget<T | null>(null);
  const deleting = useMutation(remove);
  const byScope = Map.groupBy(entries, (entry) => entry.scope);
  const scopes = workspaces
    .filter(({ kind }) => !global || kind === "global")
    .map((workspace) => ({
      ...workspace,
      library: workspace.writable && workspace.kind !== "global",
      entries: byScope.get(workspace.scope) ?? [],
    }))
    .filter(({ library, entries }) => global || library || entries.length > 0);

  return (
    <>
      {scopes.map(({ scope, label, library, entries }) => (
        <Section key={scope} title={label}>
          {entries.length > 0 ? (
            <ul className={cn("flex flex-col gap-2", listClassName)}>
              {entries.map((entry) => (
                <li key={entryKey(entry)} className="flex items-start justify-between gap-2">
                  <div className="min-w-0">{renderEntry(entry)}</div>
                  {entry.writable && (
                    <div className="flex shrink-0 gap-1">
                      {renderActions?.(entry)}
                      <WithTooltip label={labels.delete}>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={labels.delete}
                          disabled={deleting.isPending && deleting.variables === entry}
                          onClick={() => deletion.show(entry)}
                        >
                          <TrashIcon />
                        </Button>
                      </WithTooltip>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">{labels.empty}</p>
          )}
          {library && renderLibrary(scope)}
        </Section>
      ))}
      <ConfirmDialog
        open={deletion.open}
        onOpenChange={deletion.setOpen}
        title={deletion.target ? labels.deleteTitle(deletion.target) : ""}
        description={labels.deleteDescription}
        confirmLabel={labels.delete}
        onConfirm={() => {
          deletion.setOpen(false);

          if (deletion.target) deleting.mutate(deletion.target);
        }}
      />
    </>
  );
}
