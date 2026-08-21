import { useNavigate } from "@tanstack/react-router";
import { type FileUIPart } from "ai";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { AiDisclosure } from "@/components/chat/AiDisclosure";
import { ChatHeader, type ChatTab } from "@/components/chat/ChatHeader";
import { Composer } from "@/components/chat/composer/Composer";
import { MessageList } from "@/components/chat/MessageList";
import { SteeringQueue } from "@/components/chat/SteeringQueue";
import { ChatSuggestions } from "@/components/chat/Suggestions";
import { ConversationsList } from "@/components/chat/ConversationsList";
import { StreamingNavGuard } from "@/components/chat/StreamingNavGuard";
import { Tabs, TabsContent } from "@/components/ui/tabs";
import { useCompaction } from "@/hooks/chat/use-compaction";
import { useBuildRequestBody } from "@/hooks/chat/use-build-request-body";
import { useChatErrorLogger } from "@/hooks/chat/use-chat-error-logger";
import { useConversationHistory } from "@/hooks/chat/use-conversation-history";
import { useHivegentChat } from "@/hooks/chat/use-hivegent-chat";
import { SubagentLiveProvider } from "@/hooks/chat/use-subagent-live";
import { useMessageEditing } from "@/hooks/chat/use-message-editing";
import { useSteeringQueue } from "@/hooks/chat/use-steering-queue";
import { useToolOutputSync } from "@/hooks/chat/use-tool-output-sync";
import { ToolApprovalProvider, type ToolApprovalGate } from "@/hooks/chat/use-tool-approval";
import { getServerConversation, importConversation, transcribeAudio } from "@/lib/api";
import { downloadJson } from "@/lib/download";
import {
  activeChatError,
  getLastUserMessage,
  recordChatError,
} from "@/lib/chat/chat-utils";
import { type AgentMode, type ConversationArchive, type ReasoningEffort } from "@/lib/types";
import { useConversationsStore } from "@/stores/conversations-store";
import { useDocumentCanvasStore } from "@/stores/document-canvas-store";
import { useDocumentFilterStore } from "@/stores/document-filter-store";
import { useDraftHandoffStore } from "@/stores/draft-handoff-store";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
import { useSettingsStore } from "@/stores/settings-store";

interface ChatSidebarProps {
  id: string;
  draft?: boolean;
  onNewDraft?: () => void;
}

const TOOL_DENIED_REASON =
  "The user rejected this tool call, so it was not executed. " +
  "Do not call the same tool again with the same or similar arguments. " +
  "Stop working on this step, tell the user what you were about to do, and wait for their instructions.";

export function ChatSidebar({ id, draft = false, onNewDraft }: ChatSidebarProps) {
  const navigate = useNavigate();
  const addChunk = useFetchedDocumentsStore((state) => state.addChunk);
  const markFullDocument = useFetchedDocumentsStore((state) => state.markFullDocument);
  const addImage = useFetchedDocumentsStore((state) => state.addImage);
  const clearAll = useFetchedDocumentsStore((state) => state.clearAll);
  const clearFilter = useDocumentFilterStore((state) => state.clear);
  const fetchConversations = useConversationsStore((state) => state.fetchConversations);
  const conversationTitle = useConversationsStore(
    (state) => state.conversations.find((c) => c.id === id)?.title,
  );
  const setDocumentTab = useDocumentCanvasStore((state) => state.setActiveTab);
  const stashHandoff = useDraftHandoffStore((state) => state.stash);
  // Server-issued ID of a draft whose first turn finished cleanly; state
  // (not a ref) so the adoption effect below runs once it is reported.
  const [createdId, setCreatedId] = useState<string | null>(null);

  // Id of the message whose error banner the user dismissed. A persisted error
  // rides on the last message, so keying on that id both hides it for this view
  // only (`messages` stays a faithful projection of the stored conversation)
  // and re-arms the banner for the next turn, which appends a new last message.
  const [dismissedErrorId, setDismissedErrorId] = useState<string | null>(null);

  const [activeTab, setActiveTab] = useState<ChatTab>("chat");
  const [agentMode, setAgentMode] = useState<AgentMode>("interactive");
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
    regenerateTurn,
    isStreaming,
    subagentSteps,
  } = useHivegentChat(id, {
    draft,
    onConversationCreated: setCreatedId,
    requestBody: buildRequestBody,
  });

  const lastMessage = messages.at(-1);
  const chatError = activeChatError(messages, error);
  const visibleChatError = lastMessage?.id === dismissedErrorId ? undefined : chatError;

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
      await sendUserMessage({ text, files });
    },
    [sendUserMessage, messages.length, setDocumentTab],
  );

  const { queue: steeringQueue, enqueue: enqueueSteering } = useSteeringQueue(
    isStreaming,
    handleSendMessage,
  );

  // Once the first turn of a draft settles, adopt the server-issued ID on both
  // success and failure. The backend persists a turn on every finish — clean,
  // errored, or stopped — so a minted ID always names a real row; adopting only
  // on success left a failed first turn stranded on the draft URL with its row
  // invisible until a retry succeeded.
  //
  // Live SDK errors do not survive the route remount, so carry their text on the
  // last handed-off message, matching the metadata the backend stores.
  // `activeChatError` reads that back, so the recovery banner survives both the
  // handoff and a later reload.
  useEffect(() => {
    if (!draft || !createdId || messages.length === 0) return;
    if (isStreaming) return;
    if (steeringQueue.length > 0) return;
    setCreatedId(null);
    stashHandoff(createdId, recordChatError(messages, error));
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

  const { compact, isCompacting } = useCompaction({
    id,
    messages,
    isLoadingHistory,
    onRetry: handleSendMessage,
  });

  // One condition behind both compact buttons, the header's and the banner's.
  const compactDisabled = status !== "ready" || isCompacting;

  // Reset the fetched-documents panel whenever the chat identity changes —
  // this covers every navigation path, including plain links like the header
  // logo; the tool-output sync repopulates it from the loaded messages.
  // Handlers that rewrite the current conversation (edit/retry/regenerate)
  // still clear explicitly since the id stays the same there.
  useEffect(() => {
    clearAll();
  }, [id, clearAll]);

  // Runs here rather than in the context panel: a citation renders in the
  // transcript whether or not the panel is open, and it must not be shown
  // before we know the document it points at still exists.
  useToolOutputSync(messages, addChunk, markFullDocument, addImage);
  useChatErrorLogger(error, id, messages, buildRequestBody);

  const handleEditMessage = useCallback(
    async (messageId: string, newText: string) => {
      clearEditing();
      clearAll();
      await sendUserMessage({ text: newText, messageId });
    },
    [sendUserMessage, clearAll, clearEditing],
  );

  const approvalGate = useMemo<ToolApprovalGate>(
    () => ({
      decide: (approvalId, approved) =>
        void addToolApprovalResponse({
          id: approvalId,
          approved,
          reason: approved ? undefined : TOOL_DENIED_REASON,
        }),
      // The SDK records but does not dispatch a decision made while the
      // previous turn's final chunks are still draining, so the buttons wait
      // for it to settle.
      blockedReason: isStreaming ? "Available once the current response finishes." : undefined,
    }),
    [addToolApprovalResponse, isStreaming],
  );

  const handleRegenerate = useCallback(async () => {
    clearAll();
    await regenerateTurn();
  }, [regenerateTurn, clearAll]);

  const handleRetry = useCallback(async () => {
    const last = getLastUserMessage(messages);
    if (!last) return;
    clearAll();
    await sendUserMessage({ text: last.text, files: last.files, messageId: last.id });
  }, [messages, sendUserMessage, clearAll]);

  // Leaves plan mode for the default one, so the plan's writes are carried out
  // but each is still confirmed by the user.
  const handleExecutePlan = useCallback(async () => {
    setAgentMode("interactive");
    await sendUserMessage({ text: "Execute the plan." }, buildRequestBody("interactive"));
  }, [buildRequestBody, sendUserMessage]);

  const handleNewChat = useCallback(async () => {
    clearFilter();
    setActiveTab("chat");
    // On a draft we are already at "/", so navigating there is a no-op that
    // would leave a dirty chat (e.g. one stuck on an error) untouched. Mint a
    // fresh draft id instead: that remounts the chat with clean SDK state and
    // adoption refs. An empty, error-free draft is already new, so leave it
    // (and any text the user is typing) alone.
    if (draft && onNewDraft && (messages.length > 0 || error)) {
      onNewDraft();
      return;
    }
    // The new chat is a draft until its first message mints a server ID.
    await navigate({ to: "/" });
  }, [draft, onNewDraft, messages.length, error, clearFilter, navigate]);

  // Export both halves of the conversation. `frontend` is what the sidebar
  // holds in memory — the visible active path, including a turn that errored
  // and was never persisted — so a failing conversation can be sent for
  // debugging; `backend` is the persisted copy, and the only place the system
  // prompts each turn ran under can be read (the stream never carries them to
  // the browser). A draft has no server copy yet, so that half stays null. The
  // archive round-trips through the import route.
  const serverId = draft ? createdId : id;
  const handleExport = useCallback(async () => {
    const archive: ConversationArchive = {
      backend: serverId ? await getServerConversation(serverId) : null,
      frontend: {
        id,
        title: conversationTitle ?? null,
        exported_at: new Date().toISOString(),
        error: error?.message ?? null,
        messages,
      },
    };
    downloadJson(archive, `conversation-${id}.json`);
  }, [id, serverId, conversationTitle, error, messages]);

  const handleConversationSelect = useCallback(
    async (conversationId: string) => {
      clearFilter();
      setActiveTab("chat");
      await navigate({ to: "/conversations/$id", params: { id: conversationId } });
    },
    [clearFilter, navigate],
  );

  const importInputRef = useRef<HTMLInputElement>(null);

  const handleImportFile = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      // Reset so re-picking the same file fires onChange again.
      event.target.value = "";
      if (!file) return;
      try {
        const summary = await importConversation(file);
        await fetchConversations();
        await handleConversationSelect(summary.id);
        toast.success("Conversation imported");
      } catch {
        toast.error("Failed to import conversation");
      }
    },
    [fetchConversations, handleConversationSelect],
  );

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
    <Tabs
      value={activeTab}
      onValueChange={(value) => setActiveTab(value as ChatTab)}
      className="flex h-full flex-col"
    >
      <StreamingNavGuard isStreaming={isStreaming} onStop={stop} />
      <ChatHeader
        activeTab={activeTab}
        hasMessages={messages.length > 0}
        compactDisabled={compactDisabled}
        onCompact={() => compact()}
        onNewChat={handleNewChat}
        onHistoryClick={() => fetchConversations()}
        onImport={() => importInputRef.current?.click()}
        onExport={messages.length > 0 ? handleExport : undefined}
      />
      <input
        ref={importInputRef}
        type="file"
        accept="application/json,.json"
        className="hidden"
        aria-label="Import conversation file"
        onChange={handleImportFile}
      />

      <TabsContent value="chat" className="flex min-h-0 flex-1 flex-col">
        <SubagentLiveProvider value={subagentSteps}>
          <ToolApprovalProvider value={approvalGate}>
            <MessageList
              messages={messages}
              status={status}
              chatError={visibleChatError}
              compactDisabled={compactDisabled}
              isLoadingHistory={isLoadingHistory}
              compactedFrom={compactedFrom}
              editingId={editingId}
              onNavigatePrevious={handleConversationSelect}
              onRetry={handleRetry}
              onCompact={() => void compact(true)}
              onDismissError={() => {
                clearError();
                setDismissedErrorId(lastMessage?.id ?? null);
              }}
              onSetEditing={setEditing}
              onCancelEdit={clearEditing}
              onSubmitEdit={handleEditMessage}
              onRegenerate={handleRegenerate}
              onExecutePlan={agentMode === "plan" ? handleExecutePlan : undefined}
            />
          </ToolApprovalProvider>
        </SubagentLiveProvider>

        <div className="border-t p-4 space-y-3">
          {messages.length === 0 && <ChatSuggestions onSelect={handleSendMessage} />}
          <SteeringQueue queue={steeringQueue} />
          <Composer
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
