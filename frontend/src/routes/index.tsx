import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { FileSearch, Loader2 } from "lucide-react";
import { useEffect, useRef } from "react";

import { SignInButton } from "../components/SignInButton";
import { createConversation } from "../lib/api";
import { useAuth } from "../lib/auth-context";

export const Route = createFileRoute("/")({
  component: IndexPage,
});

function IndexPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const redirectingRef = useRef(false);

  const handlePostSignIn = async () => {
    const id = await createConversation();
    await navigate({ to: "/conversations/$id", params: { id } });
  };

  // Auto-redirect authenticated users to a new conversation
  useEffect(() => {
    if (!auth.isAuthenticated || auth.isLoading || redirectingRef.current) return;
    redirectingRef.current = true;
    createConversation()
      .then((id) => {
        void navigate({ to: "/conversations/$id", params: { id } });
      })
      .catch((error) => {
        console.error("Failed to create conversation:", error);
        redirectingRef.current = false;
      });
  }, [auth.isAuthenticated, auth.isLoading, navigate]);

  // Show loading while auth is initializing or redirecting
  if (auth.isLoading || auth.isAuthenticated) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // Not authenticated - show landing page with sign in button
  return (
    <div className="flex h-full items-center justify-center">
      <div className="flex flex-col items-center gap-6 text-center">
        <div className="flex items-center gap-3">
          <FileSearch className="h-12 w-12 text-primary" />
          <h1 className="text-4xl font-bold">Hivegent</h1>
        </div>
        <p className="text-lg text-muted-foreground max-w-md">
          Your intelligent document assistant powered by RAG. Upload documents and chat with your
          knowledge base.
        </p>
        <SignInButton onSignedIn={handlePostSignIn} />
      </div>
    </div>
  );
}
