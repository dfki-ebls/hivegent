import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { skillQueries } from "@/api/skills";
import { PageHeader } from "@/components/page-header";
import { SkillsSection } from "@/components/skills-section";
import { keyPrefix } from "@/i18n";

const SKILLS_T = keyPrefix(($) => $.settings.skills);

export const Route = createFileRoute("/settings/skills")({
  loader: ({ context }) =>
    context.queryClient.query({ ...skillQueries.list(), staleTime: "static" }),
  component: SkillsPage,
});

function SkillsPage() {
  const { t } = useTranslation(undefined, SKILLS_T);

  return (
    <>
      <PageHeader title={t(($) => $.title)} description={t(($) => $.description)} />
      <SkillsSection />
    </>
  );
}
