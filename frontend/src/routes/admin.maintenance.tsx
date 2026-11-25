import { useMutation, useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { RefreshCwIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { adminMutations, adminQueries, adminReindex } from "@/api/admin";
import { PageHeader } from "@/components/page-header";
import { DangerZone } from "@/components/settings/danger-zone";
import { Section } from "@/components/settings/section";
import { Field, FieldContent, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import { keyPrefix } from "@/i18n";
import { errorMessage } from "@/lib/utils";

const MAINTENANCE_T = keyPrefix(($) => $.settings.admin.maintenance);

export const Route = createFileRoute("/admin/maintenance")({ component: MaintenancePage });

/**
 * The operational switches, the one thing an admin changes here. Wiping
 * another user or a group runs on the host (`hivegent reset user|group <id>`).
 */
function MaintenancePage() {
  const { t } = useTranslation(undefined, MAINTENANCE_T);
  // The switch stays disabled until the server state is known, so it never shows a guess.
  const { data: enabled } = useQuery(adminQueries.maintenance());
  const toggle = useMutation({
    ...adminMutations.maintenance(),
    onSuccess: (next) =>
      toast.add({ type: "success", title: next ? t(($) => $.enabled) : t(($) => $.disabled) }),
    onError: (error) =>
      toast.add({
        type: "error",
        title: t(($) => $.toggleFailed),
        description: errorMessage(error),
      }),
  });

  return (
    <>
      <PageHeader title={t(($) => $.title)} description={t(($) => $.description)} />
      <Section title={t(($) => $.label)}>
        <Field orientation="horizontal" data-disabled={enabled === undefined}>
          <FieldContent>
            <FieldLabel htmlFor="maintenance-mode">{t(($) => $.label)}</FieldLabel>
            <FieldDescription>{t(($) => $.hint)}</FieldDescription>
          </FieldContent>
          <Switch
            id="maintenance-mode"
            checked={enabled ?? false}
            disabled={enabled === undefined}
            onCheckedChange={(next) => toggle.mutate(next)}
          />
        </Field>
      </Section>
      <DangerZone
        title={t(($) => $.danger.title)}
        description={t(($) => $.danger.description)}
        entries={[
          {
            key: "reindex",
            icon: RefreshCwIcon,
            title: t(($) => $.reindex.label),
            description: t(($) => $.reindex.description),
            confirm: t(($) => $.reindex.confirm),
            run: async () => (await adminReindex()).message,
          },
        ]}
      />
    </>
  );
}
