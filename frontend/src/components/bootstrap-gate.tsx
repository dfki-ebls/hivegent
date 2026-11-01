import { useQuery } from "@tanstack/react-query";
import { TriangleAlertIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { settingsQueries } from "@/api/settings";
import { errorMessage } from "@/lib/utils";

import { ConnectingScreen } from "@/components/connecting-screen";
import { StatusPanel } from "@/components/common/status-panel";

/**
 * Drive app startup from inside React so the connection spinner stays on screen
 * the whole time. Rendering no longer waits on the OIDC config: this gate waits
 * for the backend (health), fetches the runtime config, and bootstraps OIDC,
 * then reveals the app. A backend that is still booting therefore shows the
 * spinner instead of crashing the pre-render bootstrap to a blank page.
 */
export function BootstrapGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { status, error } = useQuery(settingsQueries.bootstrap());

  if (status === "success") return children;

  if (status === "error") {
    return (
      <StatusPanel
        icon={TriangleAlertIcon}
        title={t(($) => $.app.startup.failed)}
        description={errorMessage(error)}
        className="h-dvh"
      />
    );
  }

  return <ConnectingScreen />;
}
