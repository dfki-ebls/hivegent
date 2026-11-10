import { useChat } from "@ai-sdk/react";
import { ArrowDownIcon, MessagesSquareIcon, RefreshCcwIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";
import { ProcessingTrace } from "@/components/chat/chain-of-thought";
import { useChatContext } from "@/components/chat/chat-context";
import { MessageBubble } from "@/components/chat/message-bubble";
import { ErrorAlert } from "@/components/common/error-alert";
import { StatusPanel } from "@/components/common/status-panel";
import { Button } from "@/components/ui/button";
import { useChatActions } from "@/hooks/chat/use-chat-actions";
import { useToolOutputSync } from "@/hooks/chat/use-tool-output-sync";
import { keyPrefix } from "@/i18n";
import {
  activeChatError,
  isChatBusy,
  isProcessing,
  lastUserIndex,
  placeCompactions,
} from "@/lib/chat/chat-utils";

const T_OPTIONS = keyPrefix(($) => $.chat.messages);

/** The transcript of the chat being rendered, with its run error and the line that says the model works. */
export function MessageList() {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const { t: tRoot } = useTranslation();
  const { chat, conversationId } = useChatContext();
  // A stored conversation follows the turn still running at the server after a reload.
  const {
    messages: streamed,
    status,
    error,
    clearError,
  } = useChat({
    chat,
    throttle: 50,
    resume: conversationId !== null,
  });
  const { retry } = useChatActions();
  // A compaction shows where the server stores it, as after a reload.
  const messages = useMemo(() => placeCompactions(streamed), [streamed]);
  const processing = isProcessing(messages, status);
  // Only the turn the user sent last anchors: when one item swaps for another, the scroller
  // jumps to the first anchor it never scrolled to, the oldest message of a loaded conversation.
  const anchorIndex = lastUserIndex(messages);
  // The message whose error the user dismissed. A stored error rides on the last message,
  // so this hides it for this view only and re-arms for the next turn.
  const [dismissedErrorId, setDismissedErrorId] = useState<string | null>(null);
  const lastId = messages.at(-1)?.id;
  const chatError = lastId === dismissedErrorId ? undefined : activeChatError(messages, error);

  // A citation renders in the transcript whether or not the context panel is open.
  useToolOutputSync(messages);

  return (
    <MessageScrollerProvider autoScroll defaultScrollPosition="last-anchor">
      <MessageScroller className="min-h-0 flex-1">
        <MessageScrollerViewport>
          <MessageScrollerContent aria-busy={isChatBusy(status)} className="gap-6 p-4">
            {messages.length === 0 && !chatError && (
              <MessageScrollerItem className="flex flex-1">
                <StatusPanel
                  icon={MessagesSquareIcon}
                  title={t(($) => $.emptyTitle)}
                  description={t(($) => $.emptyDescription)}
                />
              </MessageScrollerItem>
            )}
            {messages.map((message, index) => (
              // Rendered in full: a reply often is many times the placeholder height that content-visibility
              // reserves offscreen, so laying it out first while scrolling up shook the transcript.
              <MessageScrollerItem
                key={message.id}
                messageId={message.id}
                scrollAnchor={index === anchorIndex}
                className="[content-visibility:visible]"
              >
                <MessageBubble
                  message={message}
                  processing={processing && index === messages.length - 1}
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
                <ErrorAlert
                  title={tRoot(($) => $.common.states.error)}
                  onDismiss={() => {
                    clearError();
                    setDismissedErrorId(lastId ?? null);
                  }}
                  action={
                    <Button variant="outline" size="sm" onClick={retry}>
                      <RefreshCcwIcon data-icon="inline-start" />
                      {tRoot(($) => $.common.actions.retry)}
                    </Button>
                  }
                >
                  {chatError}
                </ErrorAlert>
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
