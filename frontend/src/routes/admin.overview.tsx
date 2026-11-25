import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { adminQueries } from "@/api/admin";
import { PageHeader } from "@/components/page-header";
import { Section } from "@/components/settings/section";
import { Item, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item";
import { keyPrefix } from "@/i18n";

const OVERVIEW_T = keyPrefix(($) => $.settings.admin.overview);

export const Route = createFileRoute("/admin/overview")({
  loader: ({ context }) => context.queryClient.query(adminQueries.overview()),
  component: OverviewPage,
});

interface Row {
  id: string;
  counts: string[];
}

function Rows({ rows, empty }: { rows: Row[]; empty: string }) {
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>;

  return (
    <ItemGroup>
      {rows.map((row) => (
        <Item key={row.id} variant="outline" size="sm">
          <ItemContent>
            <ItemTitle className="font-mono">{row.id}</ItemTitle>
            <ItemDescription>{row.counts.join(", ")}</ItemDescription>
          </ItemContent>
        </Item>
      ))}
    </ItemGroup>
  );
}

/** The users and groups that stored something, each with what it holds. */
function OverviewPage() {
  const { t } = useTranslation(undefined, OVERVIEW_T);
  const { data } = useSuspenseQuery(adminQueries.overview());

  return (
    <>
      <PageHeader title={t(($) => $.title)} description={t(($) => $.description)} />
      <Section title={t(($) => $.users)}>
        <Rows
          empty={t(($) => $.noUsers)}
          rows={data.users.map((user) => ({
            id: user.id,
            counts: [
              t(($) => $.documents, { count: user.document_count }),
              t(($) => $.conversations, { count: user.conversation_count }),
            ],
          }))}
        />
      </Section>
      <Section title={t(($) => $.groups)}>
        <Rows
          empty={t(($) => $.noGroups)}
          rows={data.groups.map((group) => ({
            id: group.id,
            counts: [t(($) => $.documents, { count: group.document_count })],
          }))}
        />
      </Section>
    </>
  );
}
