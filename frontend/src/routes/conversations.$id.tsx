import { createFileRoute } from "@tanstack/react-router";

import { ChatLayout } from "../components/ChatLayout";

export const Route = createFileRoute("/conversations/$id")({
  component: ChatPage,
});

function ChatPage() {
  const { id } = Route.useParams();
  return <ChatLayout id={id} />;
}
