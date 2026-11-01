import { Link } from "@tanstack/react-router";
import { CompassIcon, MoveLeftIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StatusPanel } from "@/components/common/status-panel";
import { Button } from "@/components/ui/button";

/**
 * What a route shows when nothing matches the URL or its loader finds nothing,
 * the router's `defaultNotFoundComponent`, with a way back home.
 */
export function NotFound() {
  const { t } = useTranslation();

  return (
    <StatusPanel
      icon={CompassIcon}
      title={t(($) => $.app.notFound.title)}
      description={t(($) => $.app.notFound.description)}
      className="h-full"
      action={
        <Button nativeButton={false} render={<Link to="/" />}>
          <MoveLeftIcon data-icon="inline-start" />
          {t(($) => $.app.notFound.backHome)}
        </Button>
      }
    />
  );
}
