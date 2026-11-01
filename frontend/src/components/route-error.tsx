import { type ErrorComponentProps, useRouter } from "@tanstack/react-router";
import { TriangleAlertIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StatusPanel } from "@/components/common/status-panel";
import { Button } from "@/components/ui/button";
import { CodeBlock, CodeBlockContent } from "@/components/ui/code-block";
import { errorMessage } from "@/lib/utils";
import { clearAllStorage } from "@/stores/storage";

/**
 * What a route shows when it fails to load or render, the router's
 * `defaultErrorComponent`. Retrying reruns the loaders, clearing local data
 * recovers from browser state the failure may have left behind.
 */
export function RouteError({ error }: ErrorComponentProps) {
  const { t } = useTranslation();
  const router = useRouter();

  return (
    <StatusPanel
      icon={TriangleAlertIcon}
      title={t(($) => $.app.routeError.title)}
      description={t(($) => $.app.routeError.description)}
      className="h-full"
      action={
        <>
          <CodeBlock size="sm" variant="destructive">
            <CodeBlockContent wrap className="max-h-32">
              {errorMessage(error)}
            </CodeBlockContent>
          </CodeBlock>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Button onClick={() => void router.invalidate()}>
              {t(($) => $.common.actions.retry)}
            </Button>
            <Button variant="outline" onClick={clearAllStorage}>
              {t(($) => $.app.routeError.clearData)}
            </Button>
          </div>
        </>
      }
    />
  );
}
