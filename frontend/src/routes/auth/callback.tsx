import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { createConversation } from "../../lib/api";
import { isOidcConfigured } from "../../lib/auth-config";
import { useAuth } from "../../lib/auth-context";

export const Route = createFileRoute("/auth/callback")({
  component: CallbackPage,
});

function CallbackPage() {
  const navigate = useNavigate();
  const auth = useAuth();
  const [isCreatingChat, setIsCreatingChat] = useState(false);

  useEffect(() => {
    // If OIDC is not configured, redirect to home
    if (!isOidcConfigured()) {
      navigate({ to: "/" });
      return;
    }

    // Handle the OIDC callback
    if (auth.isAuthenticated && !isCreatingChat) {
      // Successfully authenticated, create a new chat and navigate to it
      setIsCreatingChat(true);
      createConversation()
        .then((id) => {
          navigate({ to: "/chat/$id", params: { id } });
        })
        .catch((error) => {
          console.error("Failed to create conversation:", error);
          navigate({ to: "/" });
        });
    } else if (auth.error) {
      // Error during authentication
      console.error("Authentication error:", auth.error);
      navigate({ to: "/" });
    }
    // While auth.isLoading, we show the loading state
  }, [auth.isAuthenticated, auth.error, auth.isLoading, navigate, isCreatingChat]);

  return (
    <div className="flex h-full items-center justify-center">
      <div className="flex flex-col items-center gap-4">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        <p className="text-muted-foreground">
          {isCreatingChat ? "Starting new chat..." : "Completing sign in..."}
        </p>
      </div>
    </div>
  );
}
