import { useNavigate } from "@tanstack/react-router";
import { type FileUIPart } from "ai";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { AiDisclosure } from "@/components/chat/AiDisclosure";
import { ChatHeader } from "@/components/chat/ChatHeader";
import { Composer } from "@/components/chat/composer/Composer";
import { MessageList } from "@/components/chat/MessageList";
import { SteeringQueue } from "@/components/chat/SteeringQueue";
import { ChatSuggestions } from "@/components/chat/Suggestions";
import { ConversationsList } from "@/components/chat/ConversationsList";
import { Tabs, TabsContent } from "@/components/ui/tabs";
import { useAutoCompact } from "@/hooks/chat/use-auto-compact";
import { useBuildRequestBody } from "@/hooks/chat/use-build-request-body";
import { useChatErrorLogger } from "@/hooks/chat/use-chat-error-logger";
import { useConversationHistory } from "@/hooks/chat/use-conversation-history";
import { useHivegentChat } from "@/hooks/chat/use-hivegent-chat";
import { useMessageEditing } from "@/hooks/chat/use-message-editing";
import { useSteeringQueue } from "@/hooks/chat/use-steering-queue";
import { useToolOutputSync } from "@/hooks/chat/use-tool-output-sync";
import { exportConversation } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { getLastUserMessage, isContextLengthError } from "@/lib/chat/chat-utils";
import { type AgentMode, type ReasoningEffort } from "@/lib/types";
import { useConversationsStore } from "@/stores/conversations-store";
import { useDraftHandoffStore } from "@/stores/draft-handoff-store";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";

interface ChatSidebarProps {
  id: string;
  draft?: boolean;
  includedDocuments: string[];
  excludedDocuments: string[];
  onRemoveDocument: (filename: string) => void;
  onClearDocuments: () => void;
}

export function ChatSidebar({
  id,
  draft = false,
  includedDocuments,
  excludedDocuments,
  onRemoveDocument,
  onClearDocuments,
}: ChatSidebarProps) {
  const navigate = useNavigate();
  const addChunk = useFetchedDocumentsStore((state) => state.addChunk);
  const markFullDocument = useFetchedDocumentsStore((state) => state.markFullDocument);
  const addImage = useFetchedDocumentsStore((state) => state.addImage);
  const clearAll = useFetchedDocumentsStore((state) => state.clearAll);
  const fetchConversations = useConversationsStore((state) => state.fetchConversations);
  const stashHandoff = useDraftHandoffStore((state) => state.stash);
  const createdIdRef = useRef<string | null>(null);

  const [inputValue, setInputValue] = useState("");
  const [activeTab, setActiveTab] = useState("chat");
  const [agentMode, setAgentMode] = useState<AgentMode>("execute");
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>("auto");

  const buildRequestBody = useBuildRequestBody({
    agentMode,
    reasoningEffort,
    includedDocuments,
    excludedDocuments,
  });

  const {
    messages,
    status,
    error,
    clearError,
    setMessages,
    addToolApprovalResponse,
    stop,
    sendUserMessage,
    regenerateWithBody,
    isStreaming,
  } = useHivegentChat(id, {
    draft,
    onConversationCreated: (newId) => {
      createdIdRef.current = newId;
    },
  });

  const { isLoadingHistory, compactedFrom } = useConversationHistory(id, setMessages, draft);
  const { editingId, setEditing, clear: clearEditing } = useMessageEditing(status);

  // Once the first turn of a draft has settled, adopt the server-issued ID:
  // hand the streamed messages to that route and navigate so reloads and the
  // sidebar reflect the now-persisted conversation.
  useEffect(() => {
    const newId = createdIdRef.current;
    if (!draft || !newId || status !== "ready" || messages.length === 0) return;
    createdIdRef.current = null;
    stashHandoff(newId, messages);
    void fetchConversations();
    // Replace, not push: the transient draft URL ("/") shouldn't be a
    // back-button target once it has become a real conversation.
    void navigate({ to: "/conversations/$id", params: { id: newId }, replace: true });
  }, [draft, status, messages, stashHandoff, fetchConversations, navigate]);

  const handleSendMessage = useCallback(
    async (text: string, files?: FileUIPart[]) => {
      if (!text.trim() && (!files || files.length === 0)) return;
      setInputValue("");
      onClearDocuments();
      await sendUserMessage({ text, files }, buildRequestBody());
    },
    [buildRequestBody, sendUserMessage, onClearDocuments],
  );

  const { queue: steeringQueue, enqueue: enqueueSteering } = useSteeringQueue(
    isStreaming,
    handleSendMessage,
  );

  const {
    compact,
    isCompacting,
    error: compactionError,
    clearError: clearCompactionError,
  } = useAutoCompact({
    id,
    chatError: error,
    messages,
    isLoadingHistory,
    onRetry: handleSendMessage,
  });

  useToolOutputSync(messages, addChunk, markFullDocument, addImage);
  useChatErrorLogger(error, id, messages, buildRequestBody);

  const handleEditMessage = useCallback(
    async (messageId: string, newText: string) => {
      clearEditing();
      clearAll();
      await sendUserMessage({ text: newText, messageId }, buildRequestBody());
    },
    [buildRequestBody, sendUserMessage, clearAll, clearEditing],
  );

  const handleRegenerate = useCallback(async () => {
    clearAll();
    await regenerateWithBody(buildRequestBody());
  }, [buildRequestBody, regenerateWithBody, clearAll]);

  const handleRetry = useCallback(async () => {
    const last = getLastUserMessage(messages);
    if (!last) return;
    clearAll();
    await sendUserMessage({ text: last.text, messageId: last.id }, buildRequestBody());
  }, [messages, buildRequestBody, sendUserMessage, clearAll]);

  const handleExecutePlan = useCallback(async () => {
    setAgentMode("execute");
    await sendUserMessage({ text: "Execute the plan." }, buildRequestBody("execute"));
    onClearDocuments();
  }, [buildRequestBody, sendUserMessage, onClearDocuments]);

  const handleNewChat = useCallback(async () => {
    clearAll();
    setActiveTab("chat");
    // The new chat is a draft until its first message mints a server ID.
    await navigate({ to: "/" });
  }, [clearAll, navigate]);

  const handleExport = useCallback(async () => {
    try {
      const blob = await exportConversation(id);
      downloadBlob(blob, `conversation-${id}.json`);
    } catch {
      toast.error("Failed to export conversation");
    }
  }, [id]);

  const handleConversationSelect = useCallback(
    async (conversationId: string) => {
      clearAll();
      setActiveTab("chat");
      await navigate({ to: "/conversations/$id", params: { id: conversationId } });
    },
    [clearAll, navigate],
  );

  const handleNavigateToPrevious = useCallback(
    (previousId: string) => {
      clearAll();
      void navigate({ to: "/conversations/$id", params: { id: previousId } });
    },
    [clearAll, navigate],
  );

  const handleTranscriptionChange = useCallback((text: string) => {
    setInputValue((prev) => (prev ? `${prev} ${text}` : text));
  }, []);

  return (
    <Tabs value={activeTab} onValueChange={setActiveTab} className="flex h-full flex-col">
      <ChatHeader
        hasMessages={messages.length > 0}
        compactDisabled={status !== "ready" || isCompacting}
        onCompact={() => compact()}
        onNewChat={handleNewChat}
        onHistoryClick={() => fetchConversations()}
        onExport={draft ? undefined : handleExport}
      />

      <TabsContent value="chat" className="flex min-h-0 flex-1 flex-col">
        <MessageList
          messages={messages}
          status={status}
          chatError={error}
          compactionError={compactionError}
          isLoadingHistory={isLoadingHistory}
          isCompacting={isCompacting}
          compactedFrom={compactedFrom}
          editingId={editingId}
          showChatError={!!error && !isContextLengthError(error)}
          onNavigatePrevious={handleNavigateToPrevious}
          onRetry={handleRetry}
          onDismissError={() => {
            clearCompactionError();
            clearError();
          }}
          onSetEditing={setEditing}
          onCancelEdit={clearEditing}
          onSubmitEdit={handleEditMessage}
          onRegenerate={handleRegenerate}
          onApprove={(approvalId) => addToolApprovalResponse({ id: approvalId, approved: true })}
          onDeny={(approvalId) => addToolApprovalResponse({ id: approvalId, approved: false })}
          onExecutePlan={agentMode === "plan" ? handleExecutePlan : undefined}
        />

        <div className="border-t p-4 space-y-3">
          {messages.length === 0 && <ChatSuggestions onSelect={handleSendMessage} />}
          <SteeringQueue queue={steeringQueue} />
          <Composer
            value={inputValue}
            onChange={setInputValue}
            onSubmit={(text, files) => {
              if (isStreaming) enqueueSteering(text);
              else void handleSendMessage(text, files);
            }}
            status={status}
            onStop={stop}
            isStreaming={isStreaming}
            agentMode={agentMode}
            onAgentModeChange={setAgentMode}
            reasoningEffort={reasoningEffort}
            onReasoningEffortChange={setReasoningEffort}
            includedDocuments={includedDocuments}
            excludedDocuments={excludedDocuments}
            onRemoveDocument={onRemoveDocument}
            onTranscriptionChange={handleTranscriptionChange}
          />
          <AiDisclosure />
        </div>
      </TabsContent>

      <TabsContent value="history" className="min-h-0 flex-1">
        <ConversationsList
          currentConversationId={id}
          onConversationSelect={handleConversationSelect}
          onActiveConversationDeleted={handleNewChat}
        />
      </TabsContent>
    </Tabs>
  );
}
