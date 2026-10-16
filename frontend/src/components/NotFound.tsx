import { Link } from "@tanstack/react-router";
import { CompassIcon, MoveLeftIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";

/**
 * Catch-all screen rendered by the root route whenever no route matches the
 * current URL. It lives inside the main outlet, so the header chrome stays in
 * place and the user can navigate straight back home.
 */
export function NotFound() {
  const { t } = useTranslation();

  return (
    <EmptyState
      icon={<CompassIcon />}
      title={t(($) => $.app.notFound.title)}
      description={t(($) => $.app.notFound.description)}
      className="h-full"
    >
      <Button nativeButton={false} render={<Link to="/" />}>
        <MoveLeftIcon data-icon="inline-start" />
        {t(($) => $.app.notFound.backHome)}
      </Button>
    </EmptyState>
  );
}
