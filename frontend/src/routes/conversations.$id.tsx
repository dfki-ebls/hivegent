import { createFileRoute } from "@tanstack/react-router";

import { getConversationMessages } from "@/api/conversations";
import { ChatLayout } from "@/components/chat-layout";
import { LoadingPanel } from "@/components/common/status-panel";
import { orNotFound } from "@/lib/not-found";
import { enforceLogin } from "@/oidc";

export const Route = createFileRoute("/conversations/$id")({
  beforeLoad: enforceLogin,
  // Each visit waits for the stored messages afresh, the chat owns them from then on.
  // Another user's id shows the not-found screen.
  loader: {
    handler: ({ params }) => orNotFound(getConversationMessages(params.id)),
    staleReloadMode: "blocking",
  },
  pendingComponent: () => <LoadingPanel className="h-full" />,
  component: ChatPage,
});

function ChatPage() {
  const { id } = Route.useParams();
  const messages = Route.useLoaderData();

  // A chat per conversation, its messages load once and then live in the chat.
  return <ChatLayout key={id} id={id} messages={messages} />;
}
