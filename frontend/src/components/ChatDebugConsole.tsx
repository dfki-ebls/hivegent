/**
 * Chat with an agent about any user's conversation, to find what went wrong in it.
 *
 * Stateless like the backend route: the whole session lives in this chat and
 * is sent with every request, and nothing is stored. The report condenses the
 * session with the conversation's data replaced by placeholders, so it can be
 * shared or handed to a coding agent.
 */

import { useChat } from "@ai-sdk/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { DefaultChatTransport } from "ai";
import { FileTextIcon, MessageSquareTextIcon } from "lucide-react";
import { type FormEvent, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { keyPrefix } from "@/i18n";
import { formatDateTime } from "@/i18n/format";
import { buildLlmConfig, getAuthHeaders, writeDebugReport } from "@/lib/api";
import { type ChatMessage, isChatBusy } from "@/lib/chat/chat-utils";
import { API_BASE_URL } from "@/lib/health";
import type { DebugReport, DebugRequestConfig } from "@/lib/types";
import { errorMessage } from "@/lib/utils";
import { debugQueries } from "@/queries/debug";
import { useComposerStore } from "@/stores/composer-store";
import { useSettingsStore } from "@/stores/settings-store";
import { ChatTextarea } from "@/components/chat/composer/ChatTextarea";
import { ReasoningEffortSelector } from "@/components/chat/composer/ReasoningEffortSelector";
import { SubmitStopButton } from "@/components/chat/composer/SubmitStopButton";
import { DebugChatDialog } from "@/components/DebugChatDialog";
import { MessageList } from "@/components/chat/MessageList";
import { EmptyState, LoadingState } from "@/components/EmptyState";
import { ErrorAlert } from "@/components/ErrorAlert";
import { HighlightedCode } from "@/components/HighlightedCode";
import { PageHeader } from "@/components/PageHeader";
import { DialogBody, ScrollDialogContent } from "@/components/ScrollDialogContent";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { InputGroup, InputGroupAddon } from "@/components/ui/input-group";
import { Spinner } from "@/components/ui/spinner";

const T_OPTIONS = keyPrefix(($) => $.app.chatDebugger);

/**
 * The run configuration of every debugger request, read when it goes out:
 * the model overrides of the chat settings and the composer's reasoning effort.
 */
function debugRequestConfig(): DebugRequestConfig {
  return {
    llm: buildLlmConfig(useSettingsStore.getState().overrides),
    reasoning_effort: useComposerStore.getState().reasoningEffort,
  };
}

function ReportDialog({
  report,
  onClose,
}: {
  report: DebugReport | undefined;
  onClose: () => void;
}) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <Dialog open={report !== undefined} onOpenChange={(open) => !open && onClose()}>
      <ScrollDialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t(($) => $.reportTitle)}</DialogTitle>
          <DialogDescription>{t(($) => $.reportDescription)}</DialogDescription>
        </DialogHeader>
        {report && (
          <DialogBody>
            {report.masked > 0 && (
              <Alert>
                <AlertDescription>{t(($) => $.masked, { count: report.masked })}</AlertDescription>
              </Alert>
            )}
            <HighlightedCode code={report.markdown} language="markdown" copyable wrap />
          </DialogBody>
        )}
      </ScrollDialogContent>
    </Dialog>
  );
}

/** The debugging session about one conversation, which remounts for every other one. */
function DebugChat({ conversationId }: { conversationId: string }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const summary = useQuery(debugQueries.conversation(conversationId));
  const transport = useMemo(
    () =>
      new DefaultChatTransport<ChatMessage>({
        api: `${API_BASE_URL}/api/debug/conversations/${encodeURIComponent(conversationId)}/chat`,
        headers: () => getAuthHeaders(),
        body: debugRequestConfig,
      }),
    [conversationId],
  );
  const { messages, status, error, sendMessage, regenerate, stop, clearError } =
    useChat<ChatMessage>({ id: `debug-${conversationId}`, transport });
  const reasoningEffort = useComposerStore((state) => state.reasoningEffort);
  const setReasoningEffort = useComposerStore((state) => state.setReasoningEffort);
  const [editingId, setEditingId] = useState<string | null>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const report = useMutation({
    mutationFn: () => writeDebugReport(conversationId, messages, debugRequestConfig()),
    meta: { inlineError: true },
  });
  const busy = isChatBusy(status);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = textRef.current?.value.trim();

    if (!text || busy) return;

    void sendMessage({ text });
    event.currentTarget.reset();
  };

  if (summary.isPending) {
    return <LoadingState className="flex-1" />;
  }

  if (summary.isError) {
    return (
      <div className="p-6">
        <ErrorAlert title={t(($) => $.notFound)}>{errorMessage(summary.error)}</ErrorAlert>
      </div>
    );
  }

  return (
    <>
      <div className="flex shrink-0 items-center gap-4 border-b px-6 py-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{summary.data.title}</p>
          <p className="text-xs text-muted-foreground">{formatDateTime(summary.data.updated_at)}</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={busy || report.isPending || !messages.some((m) => m.role === "assistant")}
          onClick={() => report.mutate()}
        >
          {report.isPending ? (
            <Spinner data-icon="inline-start" />
          ) : (
            <FileTextIcon data-icon="inline-start" />
          )}
          {t(($) => $.report)}
        </Button>
      </div>
      {report.error && (
        <div className="shrink-0 px-6 pt-4">
          <ErrorAlert onDismiss={report.reset}>{errorMessage(report.error)}</ErrorAlert>
        </div>
      )}
      <MessageList
        messages={messages}
        messageKey={(id) => id}
        status={status}
        chatError={error?.message}
        isLoadingHistory={false}
        editingId={editingId}
        onRetry={() => void regenerate()}
        onDismissError={clearError}
        onSetEditing={setEditingId}
        onCancelEdit={() => setEditingId(null)}
        onSubmitEdit={(messageId, text) => {
          setEditingId(null);
          void sendMessage({ text, messageId });
        }}
        onRegenerate={() => void regenerate()}
      />
      <form onSubmit={submit} className="shrink-0 p-4">
        <InputGroup>
          <ChatTextarea ref={textRef} placeholder={t(($) => $.placeholder)} />
          <InputGroupAddon align="block-end">
            <ReasoningEffortSelector
              value={reasoningEffort}
              onChange={setReasoningEffort}
              className="ml-auto"
            />
            <SubmitStopButton status={status} streaming={busy} onStop={() => void stop()} />
          </InputGroupAddon>
        </InputGroup>
      </form>
      <ReportDialog report={report.data} onClose={report.reset} />
    </>
  );
}

export function ChatDebugConsole({ conversationId }: { conversationId: string | undefined }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const [dialogOpen, setDialogOpen] = useState(false);

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={t(($) => $.title)}
        description={t(($) => $.description)}
        className="shrink-0 border-b px-6 py-4"
      />
      {conversationId ? (
        <DebugChat key={conversationId} conversationId={conversationId} />
      ) : (
        <EmptyState
          icon={<MessageSquareTextIcon />}
          title={t(($) => $.emptyTitle)}
          description={t(($) => $.emptyDescription)}
          className="flex-1"
        >
          <Button onClick={() => setDialogOpen(true)}>{t(($) => $.open)}</Button>
        </EmptyState>
      )}
      <DebugChatDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}
