import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { FileSearch, Loader2 } from "lucide-react";
import { useState } from "react";

import { SignInButton } from "../components/SignInButton";
import { Button } from "../components/ui/button";
import { createConversation } from "../lib/api";
import { useAuth } from "../lib/auth-context";

export const Route = createFileRoute("/")({
  component: IndexPage,
});

function IndexPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [isLoading, setIsLoading] = useState(false);

  const handlePostSignIn = async () => {
    const id = await createConversation();
    await navigate({ to: "/chat/$id", params: { id } });
  };

  const handleNewChat = async () => {
    setIsLoading(true);
    try {
      const id = await createConversation();
      await navigate({ to: "/chat/$id", params: { id } });
    } catch (error) {
      console.error("Failed to create conversation:", error);
      setIsLoading(false);
    }
  };

  // Show loading while auth is initializing
  if (auth.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // If authenticated, show option to start a new chat
  if (auth.isAuthenticated) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex flex-col items-center gap-6 text-center">
          <div className="flex items-center gap-3">
            <FileSearch className="h-12 w-12 text-primary" />
            <h1 className="text-4xl font-bold">Hivegent</h1>
          </div>
          <p className="text-lg text-muted-foreground max-w-md">
            Your intelligent document assistant powered by RAG.
          </p>
          <Button onClick={handleNewChat} size="lg" disabled={isLoading}>
            {isLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Start New Chat
          </Button>
        </div>
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
