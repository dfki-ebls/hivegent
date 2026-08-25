import type { ChatStatus } from "ai";
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";
import { Loader } from "@/components/ai-elements/loader";
import { ChatError } from "@/components/chat/ChatError";
import { CompactionBanner } from "@/components/chat/CompactionBanner";
import { ContextLimitBanner } from "@/components/chat/ContextLimitBanner";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import {
  type ChatMessage,
  isChatBusy,
  isContextLengthError,
  showThinkingLoader,
} from "@/lib/chat/chat-utils";

interface MessageListProps {
  messages: ChatMessage[];
  status: ChatStatus;
  chatError: string | undefined;
  compactDisabled: boolean;
  isLoadingHistory: boolean;
  compactedFrom: string | null;
  editingId: string | null;
  onNavigatePrevious: (previousId: string) => void;
  onRetry: () => void;
  onCompact: () => void;
  onDismissError: () => void;
  onSetEditing: (id: string) => void;
  onCancelEdit: () => void;
  onSubmitEdit: (messageId: string, newText: string) => void;
  onRegenerate: () => void;
  onExecutePlan?: () => void;
}

export function MessageList({
  messages,
  status,
  chatError,
  compactDisabled,
  isLoadingHistory,
  compactedFrom,
  editingId,
  onNavigatePrevious,
  onRetry,
  onCompact,
  onDismissError,
  onSetEditing,
  onCancelEdit,
  onSubmitEdit,
  onRegenerate,
  onExecutePlan,
}: MessageListProps) {
  const contextLimitReached = isContextLengthError(chatError);

  if (isLoadingHistory) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <Loader />
      </div>
    );
  }

  return (
    <MessageScrollerProvider autoScroll defaultScrollPosition="last-anchor">
      <MessageScroller className="min-h-0 flex-1">
        <MessageScrollerViewport>
          <MessageScrollerContent
            aria-busy={isChatBusy(status)}
            className="gap-3 p-4"
          >
            {compactedFrom && (
              <MessageScrollerItem>
                <CompactionBanner
                  compactedFrom={compactedFrom}
                  onNavigatePrevious={onNavigatePrevious}
                />
              </MessageScrollerItem>
            )}
            {messages.length === 0 && !chatError && (
              <MessageScrollerItem className="flex min-h-[50vh]">
                <Empty className="p-8">
                  <EmptyHeader className="gap-1">
                    <EmptyTitle className="text-sm">Ask about your documents</EmptyTitle>
                    <EmptyDescription>
                      Start a conversation to search and explore your documents.
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              </MessageScrollerItem>
            )}
            {messages.map((message, index) => (
              <MessageScrollerItem
                key={message.id}
                messageId={message.id}
                scrollAnchor={message.role === "user"}
              >
                <MessageBubble
                  message={message}
                  isLastMessage={index === messages.length - 1}
                  status={status}
                  editingId={editingId}
                  onSetEditing={onSetEditing}
                  onCancelEdit={onCancelEdit}
                  onSubmitEdit={onSubmitEdit}
                  onRegenerate={onRegenerate}
                  onExecutePlan={index === messages.length - 1 ? onExecutePlan : undefined}
                />
              </MessageScrollerItem>
            ))}
            {showThinkingLoader(messages, status) && (
              <MessageScrollerItem className="flex justify-center">
                <Loader />
              </MessageScrollerItem>
            )}
            {contextLimitReached && (
              <MessageScrollerItem>
                <ContextLimitBanner
                  disabled={compactDisabled}
                  onCompact={onCompact}
                  onDismiss={onDismissError}
                />
              </MessageScrollerItem>
            )}
            {chatError && !contextLimitReached && (
              <MessageScrollerItem>
                <ChatError message={chatError} onRetry={onRetry} onDismiss={onDismissError} />
              </MessageScrollerItem>
            )}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
    </MessageScrollerProvider>
  );
}
