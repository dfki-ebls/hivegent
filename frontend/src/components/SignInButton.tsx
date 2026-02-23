import { Loader2, LogIn } from "lucide-react";
import { useState } from "react";

import { useAuth } from "../lib/auth-context";
import { Button } from "./ui/button";

interface SignInButtonProps {
  onSignedIn?: () => Promise<void>;
}

/**
 * Shared sign-in button with loading state.
 *
 * Calls `auth.signIn()` and optionally runs `onSignedIn` after
 * successful local authentication.  For OIDC the browser redirects
 * before `onSignedIn` is reached.
 */
export function SignInButton({ onSignedIn }: SignInButtonProps) {
  const auth = useAuth();
  const [isLoading, setIsLoading] = useState(false);

  const handleSignIn = async () => {
    setIsLoading(true);
    try {
      await auth.signIn();
      await onSignedIn?.();
    } catch (error) {
      console.error("Sign in failed:", error);
      setIsLoading(false);
    }
  };

  return (
    <Button onClick={handleSignIn} size="lg" disabled={isLoading}>
      {isLoading ? (
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
      ) : (
        <LogIn className="mr-2 h-4 w-4" />
      )}
      Sign In
    </Button>
  );
}
