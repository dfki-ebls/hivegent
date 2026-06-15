import { useNavigate } from "@tanstack/react-router";
import { type FileUIPart } from "ai";
import { useCallback, useEffect, useState } from "react";
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
import { exportConversation, transcribeAudio } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { getLastUserMessage, isContextLengthError } from "@/lib/chat/chat-utils";
import { type AgentMode, type ReasoningEffort } from "@/lib/types";
import { useConversationsStore } from "@/stores/conversations-store";
import { useDocumentCanvasStore } from "@/stores/document-canvas-store";
import { useDocumentFilterStore } from "@/stores/document-filter-store";
import { useDraftHandoffStore } from "@/stores/draft-handoff-store";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
import { useSettingsStore } from "@/stores/settings-store";

interface ChatSidebarProps {
  id: string;
  draft?: boolean;
}

export function ChatSidebar({ id, draft = false }: ChatSidebarProps) {
  const navigate = useNavigate();
  const addChunk = useFetchedDocumentsStore((state) => state.addChunk);
  const markFullDocument = useFetchedDocumentsStore((state) => state.markFullDocument);
  const addImage = useFetchedDocumentsStore((state) => state.addImage);
  const clearAll = useFetchedDocumentsStore((state) => state.clearAll);
  const clearFilter = useDocumentFilterStore((state) => state.clear);
  const fetchConversations = useConversationsStore((state) => state.fetchConversations);
  const setDocumentTab = useDocumentCanvasStore((state) => state.setActiveTab);
  const stashHandoff = useDraftHandoffStore((state) => state.stash);
  // Server-issued ID of a draft whose first turn finished cleanly; state
  // (not a ref) so the adoption effect below runs once it is reported.
  const [createdId, setCreatedId] = useState<string | null>(null);

  const [inputValue, setInputValue] = useState("");
  const [activeTab, setActiveTab] = useState("chat");
  const [agentMode, setAgentMode] = useState<AgentMode>("execute");
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>("auto");

  const buildRequestBody = useBuildRequestBody({ agentMode, reasoningEffort });

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
    onConversationCreated: setCreatedId,
  });

  const { isLoadingHistory, compactedFrom } = useConversationHistory(id, setMessages, draft);
  const { editingId, setEditing, clear: clearEditing } = useMessageEditing(status);

  // Deliberately leaves the input untouched: it is also invoked while the
  // user may already be typing the next message (steering-queue drain,
  // post-compaction retry), so clearing happens at the submit site instead.
  const handleSendMessage = useCallback(
    async (text: string, files?: FileUIPart[]) => {
      if (!text.trim() && (!files || files.length === 0)) return;
      // A draft streams its whole first turn before adoption navigates, and
      // ChatLayout's openChat only re-surfaces on a route change, so this is
      // the sole trigger that reveals the Context panel as documents are
      // pulled. Later turns leave the user's chosen tab alone.
      if (messages.length === 0) setDocumentTab("context");
      await sendUserMessage({ text, files }, buildRequestBody());
    },
    [buildRequestBody, sendUserMessage, messages.length, setDocumentTab],
  );

  const { queue: steeringQueue, enqueue: enqueueSteering } = useSteeringQueue(
    isStreaming,
    handleSendMessage,
  );

  // Once the first turn of a draft has settled cleanly, adopt the server-issued
  // ID: hand the streamed messages to that route and navigate so reloads and the
  // sidebar reflect the now-persisted conversation. The minted ID is already
  // adopted for the transport in `onFinish`, so a retry continues this
  // conversation regardless of navigation — the URL change is purely cosmetic.
  // That lets us stay put while the turn is in an error state: navigating would
  // discard this chat instance and with it the SDK error, hiding the in-place
  // error bar (and its retry) since the destination only receives the messages.
  // Once the error is retried into a clean turn or dismissed, navigation
  // proceeds. Queued steering messages drain first (the transport already
  // targets the adopted conversation) so their turns stream here and land in the
  // handoff instead of being cut off mid-stream by the navigation.
  useEffect(() => {
    if (!draft || !createdId || messages.length === 0) return;
    if (isStreaming || error) return;
    if (steeringQueue.length > 0) return;
    setCreatedId(null);
    stashHandoff(createdId, messages);
    void fetchConversations();
    // Replace, not push: the transient draft URL ("/") shouldn't be a
    // back-button target once it has become a real conversation.
    void navigate({ to: "/conversations/$id", params: { id: createdId }, replace: true });
  }, [
    draft,
    createdId,
    isStreaming,
    error,
    messages,
    steeringQueue,
    stashHandoff,
    fetchConversations,
    navigate,
  ]);

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

  // Reset the fetched-documents panel whenever the chat identity changes —
  // this covers every navigation path, including plain links like the header
  // logo; the tool-output sync repopulates it from the loaded messages.
  // Handlers that rewrite the current conversation (edit/retry/regenerate)
  // still clear explicitly since the id stays the same there.
  useEffect(() => {
    clearAll();
  }, [id, clearAll]);

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
  }, [buildRequestBody, sendUserMessage]);

  const handleNewChat = useCallback(async () => {
    clearFilter();
    setActiveTab("chat");
    // The new chat is a draft until its first message mints a server ID.
    await navigate({ to: "/" });
  }, [clearFilter, navigate]);

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
      clearFilter();
      setActiveTab("chat");
      await navigate({ to: "/conversations/$id", params: { id: conversationId } });
    },
    [clearFilter, navigate],
  );

  const handleNavigateToPrevious = useCallback(
    (previousId: string) => {
      clearFilter();
      void navigate({ to: "/conversations/$id", params: { id: previousId } });
    },
    [clearFilter, navigate],
  );

  const handleTranscriptionChange = useCallback((text: string) => {
    setInputValue((prev) => (prev ? `${prev} ${text}` : text));
  }, []);

  // Server-side transcription backs the recording fallback for browsers
  // without a working Web Speech API; only offered when an STT model is
  // configured on the backend.
  const sttModel = useSettingsStore((state) => state.backendDefaults?.stt_model);
  const handleAudioRecorded = useCallback(async (audio: Blob) => {
    try {
      return await transcribeAudio(audio);
    } catch {
      toast.error("Failed to transcribe audio");
      return "";
    }
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
              setInputValue("");
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
            onTranscriptionChange={handleTranscriptionChange}
            onAudioRecorded={sttModel ? handleAudioRecorded : undefined}
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
