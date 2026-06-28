import { createFileRoute } from "@tanstack/react-router";
import { LogIn } from "lucide-react";
import { useCallback, useState } from "react";

import { Logo } from "@/components/Logo";
import { VersionBadge } from "@/components/VersionBadge";
import { ChatLayout } from "@/components/ChatLayout";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useOidc } from "@/oidc";

export const Route = createFileRoute("/")({
  component: IndexPage,
});

function IndexPage() {
  const { isUserLoggedIn, login } = useOidc();
  const [isSigningIn, setIsSigningIn] = useState(false);

  if (isUserLoggedIn) {
    return <DraftConversation />;
  }

  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="flex flex-col items-center gap-6 text-center">
        <Logo className="w-60 max-w-full" />
        <div className="flex items-center gap-3">
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
  // Minting a fresh ID remounts the chat (via the `key`) with clean SDK state,
  // which is how "New chat" starts over while already on this route: a plain
  // navigate to "/" is a no-op here and would leave a stuck error in place.
  const [draftId, setDraftId] = useState(() => crypto.randomUUID());
  const newDraft = useCallback(() => setDraftId(crypto.randomUUID()), []);

  return <ChatLayout key={draftId} id={draftId} draft onNewDraft={newDraft} />;
}
