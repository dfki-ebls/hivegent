import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { PencilIcon, PlusIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { assistantMutations, assistantQueries } from "@/api/assistants";
import { WithTooltip } from "@/components/common/tooltip-button";
import { PageHeader } from "@/components/page-header";
import { AssistantDialog, type AssistantTarget } from "@/components/settings/assistant-dialog";
import { LibrarySections } from "@/components/settings/library-sections";
import { Button } from "@/components/ui/button";
import { useDialogTarget } from "@/hooks/use-dialog-target";
import { keyPrefix } from "@/i18n";

const ASSISTANTS_T = keyPrefix(($) => $.settings.assistants);

export const Route = createFileRoute("/settings/assistants")({
  loader: ({ context }) =>
    context.queryClient.query({ ...assistantQueries.list(), staleTime: "static" }),
  component: AssistantsPage,
});

/** The assistants of every scope the user sees, one card each, editable where they write. */
function AssistantsPage() {
  const { t } = useTranslation(undefined, ASSISTANTS_T);
  const { data: assistants } = useSuspenseQuery(assistantQueries.list());
  const dialog = useDialogTarget<AssistantTarget | null>(null);

  return (
    <>
      <PageHeader title={t(($) => $.title)} description={t(($) => $.description)} />
      <LibrarySections
        entries={assistants}
        remove={assistantMutations.delete()}
        labels={{
          empty: t(($) => $.empty),
          delete: t(($) => $.delete.label),
          deleteTitle: ({ name }) => t(($) => $.delete.title, { name }),
          deleteDescription: t(($) => $.delete.description),
        }}
        listClassName="gap-3"
        entryKey={(assistant) => assistant.name}
        renderEntry={(assistant) => (
          <>
            <p className="font-mono text-sm font-medium">{assistant.name}</p>
            <p className="text-sm text-muted-foreground">{assistant.description}</p>
          </>
        )}
        renderActions={(assistant) => (
          <WithTooltip label={t(($) => $.edit)}>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t(($) => $.edit)}
              onClick={() => dialog.show({ scope: assistant.scope, assistant })}
            >
              <PencilIcon />
            </Button>
          </WithTooltip>
        )}
        renderLibrary={(scope) => (
          <div>
            <Button variant="outline" size="sm" onClick={() => dialog.show({ scope })}>
              <PlusIcon data-icon="inline-start" />
              {t(($) => $.create)}
            </Button>
          </div>
        )}
      />
      <AssistantDialog target={dialog.target} open={dialog.open} onOpenChange={dialog.setOpen} />
    </>
  );
}
