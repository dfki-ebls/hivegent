import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { type PipelineKind, pipelineQueries } from "@/api/pipelines";
import { PageHeader } from "@/components/page-header";
import { Section } from "@/components/settings/section";
import { Item, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item";
import { keyPrefix } from "@/i18n";

const PIPELINES_T = keyPrefix(($) => $.settings.admin.pipelines);

export const Route = createFileRoute("/admin/pipelines")({
  loader: ({ context }) =>
    Promise.all([
      context.queryClient.query(pipelineQueries.list("conversion")),
      context.queryClient.query(pipelineQueries.list("chunking")),
    ]),
  component: PipelinesPage,
});

/** The installed pipelines of one stage, as the server labels them. */
function Stage({ stage, title }: { stage: PipelineKind; title: string }) {
  const { data: pipelines } = useSuspenseQuery(pipelineQueries.list(stage));

  return (
    <Section title={title}>
      <ItemGroup>
        {pipelines.map((pipeline) => (
          <Item key={pipeline.id} variant="outline" size="sm">
            <ItemContent>
              <ItemTitle>{pipeline.label}</ItemTitle>
              <ItemDescription className="font-mono">{pipeline.id}</ItemDescription>
              <ItemDescription>{pipeline.description}</ItemDescription>
            </ItemContent>
          </Item>
        ))}
      </ItemGroup>
    </Section>
  );
}

function PipelinesPage() {
  const { t } = useTranslation(undefined, PIPELINES_T);

  return (
    <>
      <PageHeader title={t(($) => $.title)} description={t(($) => $.description)} />
      <Stage stage="conversion" title={t(($) => $.conversion)} />
      <Stage stage="chunking" title={t(($) => $.chunking)} />
    </>
  );
}
