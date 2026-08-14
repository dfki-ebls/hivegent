
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
import { type ChatMessage, isContextLengthError, showThinkingLoader } from "@/lib/chat/chat-utils";

interface MessageListProps {
  messages: ChatMessage[];
  status: ChatStatus;
  chatError: string | undefined;
  compactionError: Error | null;
  isLoadingHistory: boolean;
  compactedFrom: string | null;
  editingId: string | null;
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
  // Context-window overflows never reach the banner, live or persisted, since
  // auto-compaction owns that case.
  const showError = !!compactionError || (!!chatError && !isContextLengthError(chatError));
  const errorMessage =
    compactionError?.message || chatError || "An error occurred while processing your request.";

  // `resize="instant"`: the default spring keeps its accumulated velocity when
  // the browser clamps the scroll at the bottom, so content growing in bursts (a
  // tool card's parameters streaming into an open accordion) overshoots and gets
  // yanked back every frame, visibly bouncing. Only the resize follow is
  // instant; the initial scroll and the scroll-to-bottom button stay smooth.
  return (
    <Conversation className="min-h-0 flex-1" resize="instant">
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
        {showThinkingLoader(messages, status) && <Loader />}
        {showError && (
          <ChatError message={errorMessage} onRetry={onRetry} onDismiss={onDismissError} />
        )}
      </ConversationContent>
      <ConversationScrollButton />
    </Conversation>
  );
}
