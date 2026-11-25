import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { adminQueries, type Integration } from "@/api/admin";
import { PageHeader } from "@/components/page-header";
import { GroupsBadge } from "@/components/settings/groups-badge";
import { Section } from "@/components/settings/section";
import { Badge } from "@/components/ui/badge";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item";
import { keyPrefix } from "@/i18n";

const INTEGRATIONS_T = keyPrefix(($) => $.settings.admin.integrations);

const KINDS = ["mcp", "database", "api"] as const;

export const Route = createFileRoute("/admin/integrations")({
  loader: ({ context }) =>
    context.queryClient.query({ ...adminQueries.integrations(), staleTime: "static" }),
  component: IntegrationsPage,
});

/** The MCP servers, databases, and APIs of the host configuration by kind, each with its tools by effect. */
function IntegrationsPage() {
  const { t } = useTranslation(undefined, INTEGRATIONS_T);
  const { data: integrations } = useSuspenseQuery(adminQueries.integrations());

  return (
    <>
      <PageHeader title={t(($) => $.title)} description={t(($) => $.description)} />
      {integrations.length === 0 && (
        <p className="text-sm text-muted-foreground">{t(($) => $.empty)}</p>
      )}
      {KINDS.map((kind) => {
        const ofKind = integrations.filter((integration) => integration.kind === kind);

        return (
          ofKind.length > 0 && (
            <Section key={kind} title={t(($) => $[kind])}>
              <ItemGroup>
                {ofKind.map((integration) => (
                  <IntegrationItem key={integration.id} integration={integration} />
                ))}
              </ItemGroup>
            </Section>
          )
        );
      })}
    </>
  );
}

function IntegrationItem({ integration }: { integration: Integration }) {
  const { t } = useTranslation(undefined, INTEGRATIONS_T);
  const mcp = integration.kind === "mcp" ? integration : undefined;

  return (
    <Item variant="outline" size="sm">
      <ItemContent>
        <ItemTitle>{integration.id}</ItemTitle>
        <ItemDescription>{mcp?.error ?? integration.host}</ItemDescription>
        <ItemDescription>
          {Object.entries(integration.tools)
            .map(([name, effect]) => `${name} (${effect})`)
            .join(", ")}
        </ItemDescription>
      </ItemContent>
      <ItemActions className="flex-wrap justify-end">
        {mcp && (
          <Badge variant={mcp.connected ? "secondary" : "destructive"}>
            {mcp.connected ? t(($) => $.connected) : t(($) => $.unreachable)}
          </Badge>
        )}
        {mcp?.trusted && <Badge variant="outline">{t(($) => $.trusted)}</Badge>}
        <GroupsBadge groups={integration.groups} />
        <Badge variant="outline">
          {t(($) => $.tools, { count: Object.keys(integration.tools).length })}
        </Badge>
      </ItemActions>
    </Item>
  );
}
