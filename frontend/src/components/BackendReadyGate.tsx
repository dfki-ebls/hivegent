import type { ReactNode } from "react";
import { useEffect, useState } from "react";

import { waitForBackendReady } from "@/lib/api";

import { Spinner } from "./ui/spinner";

const STILL_WAITING_MS = 10_000;

let cachedReady = false;

export function BackendReadyGate({ children }: { children: ReactNode }) {
  const [isReady, setIsReady] = useState(cachedReady);
  const [stillWaiting, setStillWaiting] = useState(false);

  useEffect(() => {
    if (isReady) return;
    let cancelled = false;
    void waitForBackendReady().then(() => {
      if (cancelled) return;
      cachedReady = true;
      setIsReady(true);
    });
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
