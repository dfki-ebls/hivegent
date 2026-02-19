import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";

import { useAuth } from "../lib/auth-context";

interface AuthGateProps {
  children: ReactNode;
}

/**
 * Component that gates content behind authentication.
 * Shows loading state while auth initializes, then redirects
 * unauthenticated users to home.
 */
export function AuthGate({ children }: AuthGateProps) {
  const auth = useAuth();

  // Show loading while auth is initializing
  if (auth.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // If not authenticated, show message (will be redirected by route)
  if (!auth.isAuthenticated) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-muted-foreground">Please sign in to continue.</p>
      </div>
    );
  }

  return <>{children}</>;
}
