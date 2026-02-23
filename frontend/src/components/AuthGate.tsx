import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";

import { useAuth } from "../lib/auth-context";
import { SignInButton } from "./SignInButton";

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

  // If not authenticated, show message with sign in button
  if (!auth.isAuthenticated) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <p className="text-muted-foreground">Please sign in to continue.</p>
          <SignInButton />
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
