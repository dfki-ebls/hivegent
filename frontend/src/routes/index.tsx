import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { FileSearch, LogIn } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "../components/ui/button";
import { Spinner } from "../components/ui/spinner";
import { createConversation } from "../lib/api";
import { useOidc } from "../oidc";

export const Route = createFileRoute("/")({
  component: IndexPage,
});

function IndexPage() {
  const { isUserLoggedIn, login } = useOidc();
  const navigate = useNavigate();
  const redirectingRef = useRef(false);
  const [isSigningIn, setIsSigningIn] = useState(false);

  // Auto-redirect authenticated users to a new conversation
  useEffect(() => {
    if (!isUserLoggedIn || redirectingRef.current) return;
    redirectingRef.current = true;
    createConversation()
      .then((id) => {
        void navigate({ to: "/conversations/$id", params: { id } });
      })
      .catch((error) => {
        console.error("Failed to create conversation:", error);
        redirectingRef.current = false;
      });
  }, [isUserLoggedIn, navigate]);

  // Show loading while redirecting
  if (isUserLoggedIn) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner className="h-8 w-8 text-muted-foreground" />
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
        <Button
          size="lg"
          disabled={isSigningIn}
          onClick={() => {
            setIsSigningIn(true);
            login().catch(() => setIsSigningIn(false));
          }}
        >
          {isSigningIn ? <Spinner className="mr-2 h-4 w-4" /> : <LogIn className="mr-2 h-4 w-4" />}
          Sign In
        </Button>
      </div>
    </div>
  );
}
