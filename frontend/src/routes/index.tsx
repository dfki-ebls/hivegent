import { createFileRoute } from "@tanstack/react-router";
import { LogIn } from "lucide-react";
import { useState } from "react";

import { BackendReadyGate } from "../components/BackendReadyGate";
import { Logo } from "../components/Logo";
import { VersionBadge } from "../components/VersionBadge";
import { ChatLayout } from "../components/ChatLayout";
import { Button } from "../components/ui/button";
import { Spinner } from "../components/ui/spinner";
import { useOidc } from "../oidc";

export const Route = createFileRoute("/")({
  component: IndexPage,
});

function IndexPage() {
  const { isUserLoggedIn, login } = useOidc();
  const [isSigningIn, setIsSigningIn] = useState(false);

  if (isUserLoggedIn) {
    return (
      <BackendReadyGate>
        <DraftConversation />
      </BackendReadyGate>
    );
  }

  return (
    <div className="flex h-full items-center justify-center">
      <div className="flex flex-col items-center gap-6 text-center">
        <div className="flex items-center gap-3">
          <Logo className="h-12 w-12" />
          <h1 className="text-4xl font-bold">Hivegent</h1>
          <VersionBadge className="self-start" />
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

function DraftConversation() {
  // A new chat has no server ID until its first message; key the chat on a
  // throwaway client ID purely so the SDK state survives until the server
  // mints the real one. It is never sent to or stored by the backend.
  const [draftId] = useState(() => crypto.randomUUID());
  return <ChatLayout id={draftId} draft />;
}
