import { useMutation, useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { RefreshCwIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { adminMutations, adminQueries, type SourceStatus } from "@/api/admin";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { PageHeader } from "@/components/page-header";
import { GroupsBadge } from "@/components/settings/groups-badge";
import { Section } from "@/components/settings/section";
import { Badge } from "@/components/ui/badge";
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
import { keyPrefix } from "@/i18n";
import { formatDateTime, formatNumber } from "@/i18n/format";
import { errorMessage } from "@/lib/utils";

const SOURCES_T = keyPrefix(($) => $.settings.admin.sources);

export const Route = createFileRoute("/admin/sources")({
  loader: ({ context }) =>
    context.queryClient.query({ ...adminQueries.sources(), staleTime: "static" }),
  component: SourcesPage,
});

/** The external sources of the host configuration, how each last synced, and a sync on demand, confirming held deletions. */
function SourcesPage() {
  const { t } = useTranslation(undefined, SOURCES_T);
  const { data: sources } = useSuspenseQuery(adminQueries.sources());
  const [confirming, setConfirming] = useState<string | null>(null);
  const sync = useMutation({
    ...adminMutations.syncSource(),
    onSuccess: (_data, { id }) =>
      toast.add({ type: "success", title: t(($) => $.requested, { id }) }),
    onError: (error) =>
      toast.add({ type: "error", title: t(($) => $.syncFailed), description: errorMessage(error) }),
    onSettled: () => setConfirming(null),
  });

  return (
    <>
      <PageHeader title={t(($) => $.title)} description={t(($) => $.description)} />
      <Section empty={sources.length === 0 ? t(($) => $.empty) : undefined}>
        <ItemGroup>
          {sources.map((source) => (
            <SourceItem
              key={source.id}
              source={source}
              syncing={sync.isPending && sync.variables.id === source.id}
              onSync={() => sync.mutate({ id: source.id })}
              onConfirm={() => setConfirming(source.id)}
            />
          ))}
        </ItemGroup>
      </Section>
      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(open) => !open && setConfirming(null)}
        title={t(($) => $.confirmTitle, { id: confirming ?? "" })}
        description={t(($) => $.confirmDescription)}
        confirmLabel={t(($) => $.confirmDeletes)}
        busy={sync.isPending}
        onConfirm={() => confirming && sync.mutate({ id: confirming, confirmDeletes: true })}
      />
    </>
  );
}

function SourceItem({
  source,
  syncing,
  onSync,
  onConfirm,
}: {
  source: SourceStatus;
  syncing: boolean;
  onSync: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation(undefined, SOURCES_T);
  const busy = source.running || source.requested !== null || syncing;
  const synced = source.synced_at
    ? t(($) => $.synced, { when: formatDateTime(source.synced_at) })
    : t(($) => $.never);

  return (
    <Item variant="outline" size="sm">
      <ItemContent>
        <ItemTitle>/mnt/{source.id}</ItemTitle>
        <ItemDescription>
          {[source.connector, source.host, synced].filter(Boolean).join(", ")}
        </ItemDescription>
        {source.error && (
          <ItemDescription className="text-destructive">{source.error}</ItemDescription>
        )}
      </ItemContent>
      <ItemActions className="flex-wrap justify-end">
        {source.running ? (
          <Badge variant="secondary">
            {source.total > 0
              ? t(($) => $.progress, {
                  done: formatNumber(source.done),
                  total: formatNumber(source.total),
                })
              : t(($) => $.running)}
          </Badge>
        ) : (
          source.requested && <Badge variant="secondary">{t(($) => $.waiting)}</Badge>
        )}
        {source.held_deletes > 0 && (
          <Badge variant="destructive">
            {t(($) => $.held, {
              count: source.held_deletes,
              formatted: formatNumber(source.held_deletes),
            })}
          </Badge>
        )}
        <GroupsBadge groups={source.groups} />
        <Badge variant="outline">
          {t(($) => $.items, { count: source.items, formatted: formatNumber(source.items) })}
        </Badge>
        <Badge variant="outline">
          {t(($) => $.interval, { minutes: formatNumber(source.interval_minutes) })}
        </Badge>
        <Button variant="outline" size="sm" disabled={busy} onClick={onSync}>
          <RefreshCwIcon />
          {t(($) => $.syncNow)}
        </Button>
        {source.held_deletes > 0 && (
          <Button variant="destructive" size="sm" disabled={busy} onClick={onConfirm}>
            <Trash2Icon />
            {t(($) => $.confirmDeletes)}
          </Button>
        )}
      </ItemActions>
    </Item>
  );
}
