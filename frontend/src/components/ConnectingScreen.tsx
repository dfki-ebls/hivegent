import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Spinner } from "@/components/ui/spinner";

const STILL_WAITING_MS = 10_000;

/**
 * Full-screen "Connecting to server …" spinner shown while startup waits on the
 * backend. After {@link STILL_WAITING_MS} the copy softens to reassure the user
 * a slow backend is still being waited on rather than given up on.
 */
export function ConnectingScreen() {
  const { t } = useTranslation();
  const [stillWaiting, setStillWaiting] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setStillWaiting(true), STILL_WAITING_MS);

    return () => clearTimeout(timer);
  }, []);

  return (
    <div className="flex h-screen flex-col items-center justify-center gap-3 text-muted-foreground">
      <Spinner className="h-8 w-8" />
      <p className="text-sm">
        {stillWaiting
          ? t(($) => $.app.connecting.stillConnecting)
          : t(($) => $.app.connecting.connecting)}
      </p>
    </div>
  );
}
