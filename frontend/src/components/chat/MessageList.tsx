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
import { ChatError } from "@/components/chat/ChatError";
import { CompactionBanner } from "@/components/chat/CompactionBanner";
import { ContextLimitBanner } from "@/components/chat/ContextLimitBanner";
import { MessageBubble } from "@/components/chat/MessageBubble";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker";
import { Spinner } from "@/components/ui/spinner";
import { keyPrefix } from "@/i18n";
import {
  type ChatMessage,
  isChatBusy,
  isContextLengthError,
  lastUserIndex,
  showThinkingLoader,
} from "@/lib/chat/chat-utils";

const T_OPTIONS = keyPrefix(($) => $.chat.messages);

interface MessageListProps {
  messages: ChatMessage[];
  /** Stable React key of a message, which outlives its ID adoption. */
  messageKey: (messageId: string) => string;
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
}

export function MessageList({
  messages,
  messageKey,
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
}: MessageListProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const contextLimitReached = isContextLengthError(chatError);
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
            {compactedFrom && (
              <MessageScrollerItem>
                <CompactionBanner
                  compactedFrom={compactedFrom}
                  onNavigatePrevious={onNavigatePrevious}
                />
              </MessageScrollerItem>
            )}
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
                  editingId={editingId}
                  onSetEditing={onSetEditing}
                  onCancelEdit={onCancelEdit}
                  onSubmitEdit={onSubmitEdit}
                  onRegenerate={onRegenerate}
                />
              </MessageScrollerItem>
            ))}
            {showThinkingLoader(messages, status) && (
              <MessageScrollerItem>
                <Marker render={<output />}>
                  <MarkerIcon>
                    <Spinner />
                  </MarkerIcon>
                  <MarkerContent className="shimmer">{t(($) => $.thinking)}</MarkerContent>
                </Marker>
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
        <MessageScrollerButton>
          <ArrowDownIcon />
          <span className="sr-only">{t(($) => $.scrollToEnd)}</span>
        </MessageScrollerButton>
      </MessageScroller>
    </MessageScrollerProvider>
  );
}
