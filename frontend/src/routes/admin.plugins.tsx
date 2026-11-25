import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { adminQueries } from "@/api/admin";
import { PageHeader } from "@/components/page-header";
import { Section } from "@/components/settings/section";
import { Badge } from "@/components/ui/badge";
import { Item, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item";
import { keyPrefix } from "@/i18n";

const PLUGINS_T = keyPrefix(($) => $.settings.admin.plugins);

export const Route = createFileRoute("/admin/plugins")({
  loader: ({ context }) => context.queryClient.query(adminQueries.plugins()),
  component: PluginsPage,
});

/** The enabled plugins, each with its features' tools by effect and its other contributions. */
function PluginsPage() {
  const { t } = useTranslation(undefined, PLUGINS_T);
  const { data: plugins } = useSuspenseQuery(adminQueries.plugins());

  return (
    <>
      <PageHeader title={t(($) => $.title)} description={t(($) => $.description)} />
      {plugins.length === 0 && <p className="text-sm text-muted-foreground">{t(($) => $.empty)}</p>}
      {plugins.map((plugin) => {
        const contributions = [
          { label: t(($) => $.converters), ids: plugin.converters },
          { label: t(($) => $.chunkers), ids: plugin.chunkers },
          { label: t(($) => $.skills), ids: plugin.skills },
        ].filter(({ ids }) => ids.length > 0);

        return (
          <Section key={plugin.name} title={plugin.name}>
            <ItemGroup>
              {plugin.features.map((feature) => (
                <Item key={feature.id} variant="outline" size="sm">
                  <ItemContent>
                    <ItemTitle className="font-mono">{feature.id}</ItemTitle>
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(feature.tools).map(([tool, effect]) => (
                        <Badge key={tool} variant={effect === "read" ? "secondary" : "outline"}>
                          {tool} ({effect})
                        </Badge>
                      ))}
                    </div>
                  </ItemContent>
                </Item>
              ))}
              {contributions.map(({ label, ids }) => (
                <Item key={label} size="sm">
                  <ItemContent>
                    <ItemTitle>{label}</ItemTitle>
                    <ItemDescription className="font-mono">{ids.join(", ")}</ItemDescription>
                  </ItemContent>
                </Item>
              ))}
            </ItemGroup>
          </Section>
        );
      })}
    </>
  );
}
