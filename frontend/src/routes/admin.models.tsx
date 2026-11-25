import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { settingsQueries } from "@/api/settings";
import { PageHeader } from "@/components/page-header";
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
import { formatNumber } from "@/i18n/format";

const MODELS_T = keyPrefix(($) => $.settings.admin.models);

export const Route = createFileRoute("/admin/models")({ component: ModelsPage });

/** The chat models of the catalog, with what each offers. */
function ModelsPage() {
  const { t } = useTranslation(undefined, MODELS_T);
  const { t: tAll } = useTranslation();
  // The admin route's gate loaded the settings.
  const { data: settings } = useSuspenseQuery(settingsQueries.server());

  return (
    <>
      <PageHeader title={t(($) => $.title)} description={t(($) => $.description)} />
      <Section empty={settings.models.length === 0 ? t(($) => $.empty) : undefined}>
        <ItemGroup>
          {settings.models.map((model) => (
            <Item key={model.id} variant="outline" size="sm">
              <ItemContent>
                <ItemTitle>{model.label}</ItemTitle>
                <ItemDescription className="font-mono">{model.id}</ItemDescription>
                {model.context_window !== null && (
                  <ItemDescription>
                    {t(($) => $.contextWindow, {
                      tokens: formatNumber(model.context_window),
                    })}
                  </ItemDescription>
                )}
              </ItemContent>
              <ItemActions className="flex-wrap justify-end">
                {model.id === settings.default_model && (
                  <Badge variant="secondary">{t(($) => $.default)}</Badge>
                )}
                {model.vision && <Badge variant="outline">{t(($) => $.vision)}</Badge>}
                {model.reasoning.map((effort) => (
                  <Badge key={effort} variant="outline">
                    {tAll(($) => $.options.reasoningEffort[effort])}
                  </Badge>
                ))}
              </ItemActions>
            </Item>
          ))}
        </ItemGroup>
      </Section>
    </>
  );
}
