import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { type FileUIPart, isToolUIPart } from "ai";
import { type ChangeEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  type ConversationArchive,
  conversationMutations,
  conversationQueries,
  getServerConversation,
} from "@/api/conversations";
import { AiDisclosure } from "@/components/chat/ai-disclosure";
import { useChatBusy, useChatContext, useChatSelector } from "@/components/chat/chat-context";
import { ChatHeader, type ChatTab } from "@/components/chat/chat-header";
import { Composer } from "@/components/chat/composer/composer";
import { ConversationsList } from "@/components/chat/conversations-list";
import { MessageList } from "@/components/chat/message-list";
import { SteeringQueue } from "@/components/chat/steering-queue";
import { StreamingNavGuard } from "@/components/chat/streaming-nav-guard";
import { ChatSuggestions } from "@/components/chat/suggestions";
import { Tabs, TabsContent } from "@/components/ui/tabs";
import { toast } from "@/components/ui/toast";
import { useChatActions } from "@/hooks/chat/use-chat-actions";
import { useChatErrorLogger } from "@/hooks/chat/use-chat-error-logger";
import { useSteeringQueue } from "@/hooks/chat/use-steering-queue";
import { isOpenPrompt } from "@/lib/chat/layout";
import { downloadJson } from "@/lib/download";
import { useDocumentCanvasStore } from "@/stores/document-canvas-store";
import { useDocumentFilterStore } from "@/stores/document-filter-store";

/** The chat of one conversation with its composer, and the list of every conversation in a tab beside it. */
export function ChatSidebar() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { chat, conversationId } = useChatContext();
  const { compact, stop } = useChatActions();
  const busy = useChatBusy();
  const hasMessages = useChatSelector((chat) => chat.messages.length > 0);
  // Compaction summarizes a stored conversation, so an empty one has nothing to compact.
  // Gated on busy rather than ready, so a failed turn can still be compacted,
  // and on an open approval or question, which a compaction would leave unanswerable.
  const promptOpen = useChatSelector(
    (chat) =>
      chat.lastMessage?.parts.some((part) => isToolUIPart(part) && isOpenPrompt(part, "waiting")) ??
      false,
  );
  const compactDisabled = !hasMessages || busy || promptOpen;
  const clearFilter = useDocumentFilterStore((state) => state.clear);
  const setDocumentTab = useDocumentCanvasStore((state) => state.setActiveTab);
  const { data: conversationTitle } = useQuery({
    ...conversationQueries.list(),
    select: (list) => list.find((c) => c.id === conversationId)?.title,
  });
  const [activeTab, setActiveTab] = useState<ChatTab>("chat");
  const importInputRef = useRef<HTMLInputElement>(null);

  useChatErrorLogger();

  // Leaves the input untouched: the steering queue drains while the user may
  // already type the next message, so the composer clears itself on submit.
  const send = (text: string, files: FileUIPart[]) => {
    if (!text.trim() && files.length === 0) return;

    // The first turn reveals the Context panel as documents are pulled, later turns leave the tab alone.
    if (chat.messages.length === 0) setDocumentTab("context");

    void chat.sendMessage({ text, files });
  };

  const { queue: steeringQueue, enqueue: enqueueSteering } = useSteeringQueue(busy, send);

  const resetView = () => {
    clearFilter();
    setActiveTab("chat");
  };

  const openConversation = async (id: string) => {
    resetView();
    await navigate({ to: "/conversations/$id", params: { id } });
  };

  // The index route names a new chat.
  const openNewChat = async () => {
    resetView();
    await navigate({ to: "/" });
  };

  // An empty, error-free chat is already new, so it and any text the user is typing stay.
  const newChat = () => {
    if (chat.messages.length === 0 && !chat.error) {
      resetView();
      return;
    }

    void openNewChat();
  };

  // Export both halves of the conversation. `frontend` is what this tab holds,
  // including a turn that errored and was never stored, so a failing
  // conversation can be sent for debugging, and `backend` is the stored copy
  // with the system prompts each turn ran under, which the stream never
  // carries to the browser. The archive round-trips through the import route.
  const exportArchive = async () => {
    if (!conversationId) return;

    const archive: ConversationArchive = {
      backend: await getServerConversation(conversationId),
      frontend: {
        id: conversationId,
        title: conversationTitle ?? null,
        exported_at: new Date().toISOString(),
        error: chat.error?.message ?? null,
        messages: chat.messages,
      },
    };
    downloadJson(archive, `conversation-${conversationId}.json`);
  };

  const importArchive = useMutation({
    ...conversationMutations.import(),
    onSuccess: async (summary) => {
      await openConversation(summary.id);
      toast.add({ type: "success", title: t(($) => $.chat.sidebar.imported) });
    },
  });

  const importFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    // Reset so re-picking the same file fires onChange again.
    event.target.value = "";

    if (file) importArchive.mutate(file);
  };

  return (
    <Tabs
      value={activeTab}
      onValueChange={(value) => setActiveTab(value as ChatTab)}
      className="flex h-full flex-col"
    >
      <StreamingNavGuard isStreaming={busy} onStop={stop} />
      <ChatHeader
        activeTab={activeTab}
        hasMessages={hasMessages}
        compactDisabled={compactDisabled}
        onCompact={compact}
        onNewChat={newChat}
        onImport={() => importInputRef.current?.click()}
        onExport={hasMessages ? () => void exportArchive() : undefined}
      />
      <input
        ref={importInputRef}
        type="file"
        accept="application/json,.json"
        className="hidden"
        aria-label={t(($) => $.chat.header.importFile)}
        onChange={importFile}
      />

      <TabsContent value="chat" className="flex min-h-0 flex-1 flex-col">
        <MessageList />

        <div className="flex flex-col gap-3 border-t p-4">
          {!hasMessages && <ChatSuggestions onSelect={(prompt) => send(prompt, [])} />}
          <SteeringQueue queue={steeringQueue} />
          <Composer
            onSubmit={(text, files) => {
              if (busy) enqueueSteering(text, files);
              else send(text, files);
            }}
          />
          <AiDisclosure />
        </div>
      </TabsContent>

      <TabsContent value="history" className="min-h-0 flex-1">
        <ConversationsList
          currentConversationId={conversationId ?? undefined}
          onConversationSelect={(id) => void openConversation(id)}
          onActiveConversationDeleted={() => void openNewChat()}
        />
      </TabsContent>
    </Tabs>
  );
}
