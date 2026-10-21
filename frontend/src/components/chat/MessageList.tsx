import type { ChatStatus } from "ai";
import { ArrowDownIcon, MessagesSquareIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";
import { ProcessingTrace } from "@/components/chat/ChainOfThought";
import { ChatError } from "@/components/chat/ChatError";
import { MessageBubble } from "@/components/chat/MessageBubble";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import { keyPrefix } from "@/i18n";
import {
  type ChatMessage,
  isChatBusy,
  isProcessing,
  lastUserIndex,
} from "@/lib/chat/chat-utils";

const T_OPTIONS = keyPrefix(($) => $.chat.messages);

interface MessageListProps {
  messages: ChatMessage[];
  /** Stable React key of a message, which outlives its ID adoption. */
  messageKey: (messageId: string) => string;
  status: ChatStatus;
  chatError: string | undefined;
  isLoadingHistory: boolean;
  editingId: string | null;
  onRetry: () => void;
  onDismissError: () => void;
  onSetEditing: (id: string) => void;
  onCancelEdit: () => void;
  onSubmitEdit: (messageId: string, newText: string) => void;
  onRegenerate: () => void;
}

export function MessageList({
  messages,
  messageKey,
  status,
  chatError,
  isLoadingHistory,
  editingId,
  onRetry,
  onDismissError,
  onSetEditing,
  onCancelEdit,
  onSubmitEdit,
  onRegenerate,
}: MessageListProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const processing = isProcessing(messages, status);
  // Only the turn the user sent last anchors: when one item swaps for another, the scroller
  // jumps to the first anchor it never scrolled to, the oldest message of a loaded conversation.
  const anchorIndex = lastUserIndex(messages);

  if (isLoadingHistory) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <MessageScrollerProvider autoScroll defaultScrollPosition="last-anchor">
      <MessageScroller className="min-h-0 flex-1">
        <MessageScrollerViewport>
          <MessageScrollerContent aria-busy={isChatBusy(status)} className="gap-6 p-4">
            {messages.length === 0 && !chatError && (
              <MessageScrollerItem className="flex flex-1">
                <Empty>
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <MessagesSquareIcon />
                    </EmptyMedia>
                    <EmptyTitle>{t(($) => $.emptyTitle)}</EmptyTitle>
                    <EmptyDescription>{t(($) => $.emptyDescription)}</EmptyDescription>
                  </EmptyHeader>
                </Empty>
              </MessageScrollerItem>
            )}
            {messages.map((message, index) => (
              // Rendered in full: a reply often is many times the placeholder height that content-visibility
              // reserves offscreen, so laying it out first while scrolling up shook the transcript.
              <MessageScrollerItem
                key={messageKey(message.id)}
                messageId={message.id}
                scrollAnchor={index === anchorIndex}
                className="[content-visibility:visible]"
              >
                <MessageBubble
                  message={message}
                  isLastMessage={index === messages.length - 1}
                  status={status}
                  processing={processing && index === messages.length - 1}
                  editingId={editingId}
                  onSetEditing={onSetEditing}
                  onCancelEdit={onCancelEdit}
                  onSubmitEdit={onSubmitEdit}
                  onRegenerate={onRegenerate}
                />
              </MessageScrollerItem>
            ))}
            {/* A reply carries its own processing line, so this one stands in until the reply starts. */}
            {processing && messages.at(-1)?.role === "user" && (
              <MessageScrollerItem>
                <ProcessingTrace />
              </MessageScrollerItem>
            )}
            {chatError && (
              <MessageScrollerItem>
                <ChatError message={chatError} onRetry={onRetry} onDismiss={onDismissError} />
              </MessageScrollerItem>
            )}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton>
          <ArrowDownIcon />
          <span className="sr-only">{t(($) => $.scrollToEnd)}</span>
        </MessageScrollerButton>
      </MessageScroller>
    </MessageScrollerProvider>
  );
}
