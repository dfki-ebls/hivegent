import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";
import { ChatDebugConsole } from "@/components/chat-debug-console";

export const Route = createFileRoute("/debug/chat")({
  validateSearch: z.object({ id: z.string().optional() }),
  component: ChatDebugPage,
});

function ChatDebugPage() {
  const { id } = Route.useSearch();

  return <ChatDebugConsole conversationId={id} />;
}
