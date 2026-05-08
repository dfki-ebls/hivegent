import { createFileRoute } from "@tanstack/react-router";

import { BackendReadyGate } from "../components/BackendReadyGate";
import { ChatLayout } from "../components/ChatLayout";
import { enforceLogin } from "../oidc";

export const Route = createFileRoute("/conversations/$id")({
  beforeLoad: enforceLogin,
  component: ChatPage,
});

function ChatPage() {
  const { id } = Route.useParams();
  return (
    <BackendReadyGate>
      <ChatLayout id={id} />
    </BackendReadyGate>
  );
}
