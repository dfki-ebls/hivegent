import { useMutation } from "@tanstack/react-query";
import { type LucideIcon, ShieldAlertIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Section } from "@/components/settings/section";
import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item";
import { toast } from "@/components/ui/toast";
import { useDialogTarget } from "@/hooks/use-dialog-target";
import { keyPrefix } from "@/i18n";
import { errorMessage } from "@/lib/utils";

const DANGER_T = keyPrefix(($) => $.settings.danger);

/** An action of a zone, which runs behind its confirmation. */
interface DangerAction {
  key: string;
  icon: LucideIcon;
  title: string;
  description: string;
  /** The label of the confirm button, the title by default. */
  confirm?: string;
  /** Marks the most destructive entry of a zone. */
  severe?: boolean;
  /** Returns an optional detail line for the success toast (counts, outcomes). */
  run: () => Promise<string | void>;
}

/** A zone of actions, each described next to the button that confirms and runs it. */
export function DangerZone({
  title,
  description,
  entries,
  destructive = true,
}: {
  title: string;
  description: string;
  entries: readonly DangerAction[];
  destructive?: boolean;
}) {
  const { t } = useTranslation(undefined, DANGER_T);
  const { target, open, show, setOpen } = useDialogTarget<DangerAction | null>(null);
  const confirm = useMutation({
    mutationFn: (action: DangerAction) => action.run(),
    // Any action here can change what every cached view shows.
    meta: { invalidates: "all" },
    onSuccess: (detail, action) =>
      toast.add({
        type: "success",
        title: t(($) => $.done, { action: action.title }),
        description: detail || undefined,
      }),
    onError: (error, action) =>
      toast.add({
        type: "error",
        title: t(($) => $.failed, { action: action.title }),
        description: errorMessage(error),
      }),
    onSettled: () => setOpen(false),
  });

  return (
    <Section
      icon={destructive ? ShieldAlertIcon : undefined}
      title={title}
      description={description}
      destructive={destructive}
    >
      <ItemGroup>
        {entries.map((action) => (
          <Item key={action.key} variant="outline" size="sm">
            <ItemContent>
              <ItemTitle>{action.title}</ItemTitle>
              <ItemDescription>{action.description}</ItemDescription>
            </ItemContent>
            <ItemActions>
              <Button
                variant={action.severe ? "destructive" : "outline"}
                size="sm"
                onClick={() => show(action)}
              >
                <action.icon data-icon="inline-start" />
                {action.confirm ?? action.title}
              </Button>
            </ItemActions>
          </Item>
        ))}
      </ItemGroup>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={target?.title ?? ""}
        description={target?.description ?? ""}
        confirmLabel={target?.confirm ?? target?.title ?? ""}
        busy={confirm.isPending}
        onConfirm={() => target && confirm.mutate(target)}
      />
    </Section>
  );
}
