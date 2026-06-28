import { TriangleAlertIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";

import { errorMessage } from "@/lib/utils";
import { initOidc } from "@/oidc";

import { ConnectingScreen } from "@/components/ConnectingScreen";
import { FullScreenNotice } from "@/components/FullScreenNotice";

type State = { status: "loading" } | { status: "ready" } | { status: "error"; message: string };

/**
 * Drive app startup from inside React so the connection spinner stays on screen
 * the whole time. Rendering no longer waits on the OIDC config: this gate waits
 * for the backend (health), fetches the runtime config, and bootstraps OIDC,
 * then reveals the app. A backend that is still booting therefore shows the
 * spinner instead of crashing the pre-render bootstrap to a blank page.
 */
export function BootstrapGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;

    // initOidc caches its promise, so StrictMode's double-mount reuses the one
    // in-flight bootstrap rather than racing a second.
    initOidc().then(
      () => {
        if (!cancelled) setState({ status: "ready" });
      },
      (e: unknown) => {
        if (cancelled) return;

        console.error("[bootstrap] startup failed", e);
        setState({ status: "error", message: errorMessage(e) });
      },
    );

    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "ready") return <>{children}</>;

  if (state.status === "error") {
    return (
      <FullScreenNotice
        icon={<TriangleAlertIcon className="h-12 w-12 text-destructive" />}
        title="Unable to start"
      >
        {state.message}
      </FullScreenNotice>
    );
  }

  return <ConnectingScreen />;
}
