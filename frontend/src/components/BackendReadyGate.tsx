import type { ReactNode } from "react";
import { useEffect, useState } from "react";

import { checkHealth } from "@/lib/api";

import { Spinner } from "./ui/spinner";

const POLL_INTERVAL_MS = 1000;
const STILL_WAITING_MS = 10_000;

let cachedReady = false;

export function BackendReadyGate({ children }: { children: ReactNode }) {
  const [isReady, setIsReady] = useState(cachedReady);
  const [stillWaiting, setStillWaiting] = useState(false);

  useEffect(() => {
    if (isReady) return;
    let cancelled = false;
    void (async () => {
      while (!cancelled) {
        if (await checkHealth()) {
          cachedReady = true;
          setIsReady(true);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
      }
    })();
    const timer = setTimeout(() => setStillWaiting(true), STILL_WAITING_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [isReady]);

  if (isReady) return <>{children}</>;

  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-muted-foreground">
      <Spinner className="h-8 w-8" />
      <p className="text-sm">
        {stillWaiting ? "Still connecting to server…" : "Connecting to server…"}
      </p>
    </div>
  );
}
