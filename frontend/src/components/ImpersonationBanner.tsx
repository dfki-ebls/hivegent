import { EyeIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { getImpersonation, stopImpersonation } from "@/lib/impersonation";

/**
 * Tab-wide notice shown while an admin is impersonating another user.
 *
 * Entering and leaving impersonation both trigger a full page reload, so
 * reading sessionStorage once per render is sufficient — no reactivity
 * needed.
 */
export function ImpersonationBanner() {
  const impersonation = getImpersonation();
  if (!impersonation) return null;

  return (
    <div className="flex items-center justify-center gap-3 border-b border-amber-500/40 bg-amber-500/15 px-4 py-1.5 text-sm">
      <EyeIcon className="h-4 w-4" />
      <span>
        Viewing as <span className="font-medium">{impersonation}</span>
      </span>
      <Button variant="outline" size="sm" className="h-6 px-2" onClick={stopImpersonation}>
        Exit
      </Button>
    </div>
  );
}
