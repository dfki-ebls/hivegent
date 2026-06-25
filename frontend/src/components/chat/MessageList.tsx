import type { UIMessage } from "@ai-sdk/react";
import type { ChatStatus } from "ai";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { Loader } from "@/components/ai-elements/loader";
import { ChatError } from "@/components/chat/ChatError";
import { CompactionBanner } from "@/components/chat/CompactionBanner";
import { MessageBubble } from "@/components/chat/MessageBubble";

interface MessageListProps {
  messages: UIMessage[];
  status: ChatStatus;
  chatError: Error | undefined;
  compactionError: Error | null;
  isLoadingHistory: boolean;
  compactedFrom: string | null;
  editingId: string | null;
  showChatError: boolean;
  onNavigatePrevious: (previousId: string) => void;
  onRetry: () => void;
  onDismissError: () => void;
  onSetEditing: (id: string) => void;
  onCancelEdit: () => void;
  onSubmitEdit: (messageId: string, newText: string) => void;
  onRegenerate: () => void;
  onApprove: (id: string) => void;
  onDeny: (id: string) => void;
  onExecutePlan?: () => void;
}

export function MessageList({
  messages,
  status,
  chatError,
  compactionError,
  isLoadingHistory,
  compactedFrom,
  editingId,
  showChatError,
  onNavigatePrevious,
  onRetry,
  onDismissError,
  onSetEditing,
  onCancelEdit,
  onSubmitEdit,
  onRegenerate,
  onApprove,
  onDeny,
  onExecutePlan,
}: MessageListProps) {
  const errorMessage =
    compactionError?.message ||
    chatError?.message ||
    "An error occurred while processing your request.";

  return (
    <Conversation className="min-h-0 flex-1">
      <ConversationContent className="gap-3">
        <CompactionBanner compactedFrom={compactedFrom} onNavigatePrevious={onNavigatePrevious} />
        {isLoadingHistory && <Loader />}
        {!isLoadingHistory && messages.length === 0 && !chatError && (
          <ConversationEmptyState
            title="Ask about your documents"
            description="Start a conversation to search and explore your documents."
          />
        )}
        {messages.map((message, index) => (
          <MessageBubble
            key={message.id}
            message={message}
            isLastMessage={index === messages.length - 1}
            status={status}
            editingId={editingId}
            onSetEditing={onSetEditing}
            onCancelEdit={onCancelEdit}
            onSubmitEdit={onSubmitEdit}
            onRegenerate={onRegenerate}
            onApprove={onApprove}
            onDeny={onDeny}
            onExecutePlan={index === messages.length - 1 ? onExecutePlan : undefined}
          />
        ))}
        {status === "submitted" && <Loader />}
        {(showChatError || compactionError) && (
          <ChatError message={errorMessage} onRetry={onRetry} onDismiss={onDismissError} />
        )}
      </ConversationContent>
      <ConversationScrollButton />
    </Conversation>
  );
}
