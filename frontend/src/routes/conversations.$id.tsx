import { createFileRoute } from "@tanstack/react-router";

import { ChatLayout } from "@/components/chat-layout";
import { enforceLogin } from "@/oidc";

export const Route = createFileRoute("/conversations/$id")({
  beforeLoad: enforceLogin,
  component: ChatPage,
});

function ChatPage() {
  const { id } = Route.useParams();
  return <ChatLayout id={id} />;
}
