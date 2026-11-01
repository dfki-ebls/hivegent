import { EyeIcon } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";

import { Alert, AlertAction, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { getImpersonation, stopImpersonation } from "@/lib/impersonation";

/**
 * Tab-wide notice shown while an admin is impersonating another user.
 *
 * Entering and leaving impersonation both trigger a full page reload, so
 * reading sessionStorage once per render is sufficient, no reactivity
 * needed.
 */
export function ImpersonationBanner() {
  const { t } = useTranslation();
  const impersonation = getImpersonation();

  if (!impersonation) return null;

  // Amber, since the theme has no warning token and the banner must stand out.
  return (
    <Alert className="rounded-none border-x-0 border-t-0 border-amber-500/40 bg-amber-500/15">
      <EyeIcon />
      <AlertTitle>
        <Trans
          i18nKey={($) => $.app.impersonation.viewingAs}
          values={{ user: impersonation }}
          components={{ bold: <span className="font-semibold" /> }}
        />
      </AlertTitle>
      <AlertAction>
        <Button variant="outline" size="xs" onClick={stopImpersonation}>
          {t(($) => $.app.impersonation.exit)}
        </Button>
      </AlertAction>
    </Alert>
  );
}
