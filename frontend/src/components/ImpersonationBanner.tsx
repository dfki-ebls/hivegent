import { EyeIcon } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";

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

  return (
    <div className="flex items-center justify-center gap-3 border-b border-amber-500/40 bg-amber-500/15 px-4 py-1.5 text-sm">
      <EyeIcon className="h-4 w-4" />
      <span>
        <Trans
          i18nKey={($) => $.app.impersonation.viewingAs}
          values={{ user: impersonation }}
          components={{ bold: <span className="font-medium" /> }}
        />
      </span>
      <Button variant="outline" size="sm" className="h-6 px-2" onClick={stopImpersonation}>
        {t(($) => $.app.impersonation.exit)}
      </Button>
    </div>
  );
}
