import { useQuery } from "@tanstack/react-query";
import { FileXIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { caseBaseQueries } from "@/api/case-bases";
import { LoadingPanel, StatusPanel } from "@/components/common/status-panel";
import { ScrollArea } from "@/components/ui/scroll-area";
import { errorMessage } from "@/lib/utils";
import { ConfigView } from "@/components/documents/similarity/config-view";
import { TestPanel } from "@/components/documents/similarity/test-panel";

/**
 * The similarity of the case base at *path*: its configuration inferred from
 * its data, decision by decision with why, and a test query.
 */
export function SimilarityView({ path }: { path: string }) {
  const { t } = useTranslation();
  const caseBase = useQuery(caseBaseQueries.detail(path));

  if (caseBase.error) {
    return (
      <StatusPanel
        icon={FileXIcon}
        title={t(($) => $.app.api.errors.fetchCaseBase)}
        description={errorMessage(caseBase.error)}
      />
    );
  }

  if (!caseBase.data) return <LoadingPanel />;

  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="flex flex-col gap-8 p-4">
        <ConfigView caseBase={caseBase.data} />
        <TestPanel caseBase={caseBase.data} />
      </div>
    </ScrollArea>
  );
}
