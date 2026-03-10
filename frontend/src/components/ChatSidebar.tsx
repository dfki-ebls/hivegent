import { type UIMessage, useChat } from "@ai-sdk/react";
import { useNavigate } from "@tanstack/react-router";
import {
  type FileUIPart,
  DefaultChatTransport,
  lastAssistantMessageIsCompleteWithApprovalResponses,
} from "ai";
import {
  AlertCircle,
  BrainIcon,
  CopyIcon,
  EyeOff,
  FileText,
  Folder,
  HistoryIcon,
  MessageSquareIcon,
  Minimize2,
  Paperclip,
  PencilIcon,
  RefreshCcwIcon,
  SquarePen,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Components } from "streamdown";
import { getToolPartInfo, processToolOutput, ToolPartDisplay, type ToolPart } from "./ToolParts";
import {
  API_BASE_URL,
  buildLlmConfig,
  buildToolsPayload,
  compactConversation,
  createConversation,
  getAuthHeaders,
  getConversation,
  getConversationMessages,
} from "../lib/api";
import { REASONING_EFFORT_OPTIONS, type ReasoningEffort } from "../lib/types";
import { useConversationsStore } from "../stores/conversations-store";
import { useFetchedDocumentsStore } from "../stores/fetched-documents-store";
import { useSettingsStore } from "../stores/settings-store";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "./ai-elements/conversation";
import { Loader } from "./ai-elements/loader";
import {
  Message,
  MessageAction,
  MessageActions,
  MessageContent,
  MessageResponse,
} from "./ai-elements/message";
import { Reasoning, ReasoningContent, ReasoningTrigger } from "./ai-elements/reasoning";
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputHeader,
  PromptInputSelect,
  PromptInputSelectContent,
  PromptInputSelectItem,
  PromptInputSelectTrigger,
  PromptInputSelectValue,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
  usePromptInputAttachments,
} from "./ai-elements/prompt-input";
import { SpeechInput } from "./ai-elements/speech-input";
import { Suggestion, Suggestions } from "./ai-elements/suggestion";
import { Citation } from "./Citation";
import { ConversationsList } from "./ConversationsList";
import { SettingsDialog } from "./SettingsDialog";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
import { Textarea } from "./ui/textarea";

// --- Prompt input toolbar components ---

/** Must be rendered inside <PromptInput> to access attachment context. */
function FileSelectButton() {
  const { openFileDialog } = usePromptInputAttachments();
  return (
    <Button variant="ghost" size="icon" onClick={openFileDialog}>
      <Paperclip className="h-4 w-4" />
      <span className="sr-only">Attach file</span>
    </Button>
  );
}

/** Renders badges for attached files. Must be inside <PromptInput>. */
function AttachedFiles() {
  const { files, remove } = usePromptInputAttachments();
  if (files.length === 0) return null;
  return (
    <PromptInputHeader>
      {files.map((file) => (
        <Badge key={file.id} variant="outline" className="gap-1 text-xs">
          <Paperclip className="h-3 w-3" />
          {file.filename}
          <button
            type="button"
            className="ml-0.5 rounded-full hover:bg-muted"
            onClick={() => remove(file.id)}
          >
            <X className="h-3 w-3" />
          </button>
        </Badge>
      ))}
    </PromptInputHeader>
  );
}

/** Extract text from the last user message. */
function getLastUserMessageText(messages: UIMessage[]): string | undefined {
  const lastUserMessage = [...messages].reverse().find((m) => m.role === "user");
  if (!lastUserMessage?.parts) return undefined;
  const texts = lastUserMessage.parts
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text);
  return texts.length > 0 ? texts.join("\n") : undefined;
}

/** Check if an error is a context length exceeded error. */
function isContextLengthError(error: Error | null | undefined): boolean {
  if (!error) return false;
  const msg = error.message || "";
  return msg.includes("context_length_exceeded") || msg.includes("maximum context length");
}

// --- Text part component ---

interface TextPartDisplayProps {
  text: string;
  showActions: boolean;
  onRegenerate: () => void;
}

const CITATION_ALLOWED_TAGS = { cite: ["filename", "chunk"] };
const CITATION_COMPONENTS = { cite: Citation } as Components;

function TextPartDisplay({ text, showActions, onRegenerate }: TextPartDisplayProps) {
  return (
    <div>
      <MessageResponse allowedTags={CITATION_ALLOWED_TAGS} components={CITATION_COMPONENTS}>
        {text}
      </MessageResponse>
      {showActions && (
        <MessageActions>
          <MessageAction onClick={onRegenerate} label="Retry">
            <RefreshCcwIcon className="size-3" />
          </MessageAction>
          <MessageAction onClick={() => navigator.clipboard.writeText(text)} label="Copy">
            <CopyIcon className="size-3" />
          </MessageAction>
        </MessageActions>
      )}
    </div>
  );
}

// --- User text part component (with inline editing) ---

interface UserTextPartDisplayProps {
  text: string;
  messageId: string;
  isEditing: boolean;
  onCancelEdit: () => void;
  onSubmitEdit: (messageId: string, newText: string) => void;
}

function UserTextPartDisplay({
  text,
  messageId,
  isEditing,
  onCancelEdit,
  onSubmitEdit,
}: UserTextPartDisplayProps) {
  const [editText, setEditText] = useState(text);

  useEffect(() => {
    setEditText(text);
  }, [text, isEditing]);

  if (isEditing) {
    return (
      <div className="space-y-2">
        <Textarea
          value={editText}
          onChange={(e) => setEditText(e.target.value)}
          className="min-h-[80px] resize-y"
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              onCancelEdit();
            } else if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (editText.trim()) {
                onSubmitEdit(messageId, editText);
              }
            }
          }}
        />
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onCancelEdit}>
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={() => {
              if (editText.trim()) {
                onSubmitEdit(messageId, editText);
              }
            }}
          >
            Submit
          </Button>
        </div>
      </div>
    );
  }

  return <div className="whitespace-pre-wrap">{text}</div>;
}

// --- Message part renderer ---

interface MessagePartProps {
  part: UIMessage["parts"][number];
  partIndex: number;
  isLastTextPart: boolean;
  showActions: boolean;
  isUserMessage: boolean;
  messageId: string;
  isEditing: boolean;
  onCancelEdit: () => void;
  onSubmitEdit: (messageId: string, newText: string) => void;
  onRegenerate: () => void;
  onApprove: (id: string) => void;
  onDeny: (id: string) => void;
}

function MessagePart({
  part,
  partIndex,
  isLastTextPart,
  showActions,
  isUserMessage,
  messageId,
  isEditing,
  onCancelEdit,
  onSubmitEdit,
  onRegenerate,
  onApprove,
  onDeny,
}: MessagePartProps) {
  if (part.type === "text" && isUserMessage) {
    return (
      <UserTextPartDisplay
        key={partIndex}
        text={part.text}
        messageId={messageId}
        isEditing={isEditing}
        onCancelEdit={onCancelEdit}
        onSubmitEdit={onSubmitEdit}
      />
    );
  }

  if (part.type === "text") {
    return (
      <TextPartDisplay
        key={partIndex}
        text={part.text}
        showActions={isLastTextPart && showActions}
        onRegenerate={onRegenerate}
      />
    );
  }

  if (part.type === "reasoning") {
    return (
      <Reasoning key={partIndex} isStreaming={part.state === "streaming"}>
        <ReasoningTrigger />
        <ReasoningContent>{part.text}</ReasoningContent>
      </Reasoning>
    );
  }

  if (part.type === "step-start") {
    return null;
  }

  const info = getToolPartInfo(part);
  if (info) {
    return (
      <ToolPartDisplay
        key={partIndex}
        toolName={info.toolName}
        part={part as ToolPart}
        onApprove={onApprove}
        onDeny={onDeny}
      />
    );
  }

  return null;
}

interface ChatSidebarProps {
  id: string;
  includedDocuments: string[];
  excludedDocuments: string[];
  onRemoveDocument: (filename: string) => void;
  onClearDocuments: () => void;
}

const SUGGESTED_QUESTIONS = [
  "What documents do I have?",
  "Summarize my most recent notes",
  "Find documents about meetings",
  "What are my action items?",
];

export function ChatSidebar({
  id,
  includedDocuments,
  excludedDocuments,
  onRemoveDocument,
  onClearDocuments,
}: ChatSidebarProps) {
  const navigate = useNavigate();
  const addChunk = useFetchedDocumentsStore((state) => state.addChunk);
  const markFullDocument = useFetchedDocumentsStore((state) => state.markFullDocument);
  const clearAll = useFetchedDocumentsStore((state) => state.clearAll);
  const fetchConversations = useConversationsStore((state) => state.fetchConversations);
  const { llm, smallModel, personality, customSystemMessage, toolsSpec } = useSettingsStore();
  const [inputValue, setInputValue] = useState("");
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [activeTab, setActiveTab] = useState("chat");
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>("auto");
  const [compactedFrom, setCompactedFrom] = useState<string | null>(null);
  const [isCompacting, setIsCompacting] = useState(false);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [conversationError, setConversationError] = useState(false);
  const pendingRetryRef = useRef<string | null>(null);

  const hasDocumentFilters = includedDocuments.length > 0 || excludedDocuments.length > 0;

  const handleTranscriptionChange = useCallback((text: string) => {
    setInputValue((prev) => (prev ? `${prev} ${text}` : text));
  }, []);

  const handleNewChat = async () => {
    const newId = await createConversation();
    clearAll();
    setActiveTab("chat");
    await navigate({ to: "/conversations/$id", params: { id: newId } });
  };

  const handleConversationSelect = async (conversationId: string) => {
    clearAll();
    setActiveTab("chat");
    await navigate({ to: "/conversations/$id", params: { id: conversationId } });
  };

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: `${API_BASE_URL}/api/conversations/${id}/chat`,
        headers: () => getAuthHeaders(),
      }),
    [id],
  );

  const {
    messages,
    sendMessage,
    status,
    error,
    clearError,
    regenerate,
    stop,
    setMessages,
    addToolApprovalResponse,
  } = useChat({
    id,
    transport,
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithApprovalResponses,
  });

  // Clear editing state when a response starts streaming
  useEffect(() => {
    if (status !== "ready") {
      setEditingMessageId(null);
    }
  }, [status]);

  useEffect(() => {
    let cancelled = false;
    setIsLoadingHistory(true);
    setCompactedFrom(null);
    setConversationError(false);
    void getConversation(id)
      .then(async (conv) => {
        if (cancelled) return;
        if (!conv) {
          setConversationError(true);
          return;
        }
        if (conv.compacted_from) {
          setCompactedFrom(conv.compacted_from);
        }
        const initialMessages = await getConversationMessages(id);
        if (!cancelled && initialMessages.length > 0) {
          setMessages(initialMessages);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoadingHistory(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id, setMessages]);

  const handleSendMessage = useCallback(
    async (text: string, files?: FileUIPart[]) => {
      if (!text.trim() && (!files || files.length === 0)) return;
      const authHeaders = await getAuthHeaders();
      await sendMessage(
        { text, files },
        {
          headers: authHeaders,
          body: {
            personality,
            system_message: personality === "custom" ? customSystemMessage : undefined,
            reasoning_effort: reasoningEffort,
            llm: buildLlmConfig(llm),
            included_documents: includedDocuments,
            excluded_documents: excludedDocuments,
            tools: buildToolsPayload(toolsSpec),
          },
        },
      );
      setInputValue("");
      onClearDocuments();
    },
    [
      personality,
      customSystemMessage,
      reasoningEffort,
      llm,
      includedDocuments,
      excludedDocuments,
      toolsSpec,

      sendMessage,
      onClearDocuments,
    ],
  );

  const handleEditMessage = useCallback(
    async (messageId: string, newText: string) => {
      setEditingMessageId(null);
      const authHeaders = await getAuthHeaders();
      await sendMessage(
        { text: newText, messageId },
        {
          headers: authHeaders,
          body: {
            personality,
            system_message: personality === "custom" ? customSystemMessage : undefined,
            reasoning_effort: reasoningEffort,
            llm: buildLlmConfig(llm),
            included_documents: includedDocuments,
            excluded_documents: excludedDocuments,
            tools: buildToolsPayload(toolsSpec),
          },
        },
      );
    },
    [
      personality,
      customSystemMessage,
      reasoningEffort,
      llm,
      includedDocuments,
      excludedDocuments,
      toolsSpec,

      sendMessage,
    ],
  );

  // Re-send the pending message after navigating to a compacted conversation
  useEffect(() => {
    if (isLoadingHistory || !pendingRetryRef.current) return;
    const text = pendingRetryRef.current;
    pendingRetryRef.current = null;
    void handleSendMessage(text);
  }, [isLoadingHistory, handleSendMessage]);

  const handleRegenerate = useCallback(async () => {
    const authHeaders = await getAuthHeaders();
    await regenerate({
      headers: authHeaders,
      body: {
        personality,
        system_message: personality === "custom" ? customSystemMessage : undefined,
        reasoning_effort: reasoningEffort,
        llm: buildLlmConfig(llm),
        included_documents: includedDocuments,
        excluded_documents: excludedDocuments,
        tools: buildToolsPayload(toolsSpec),
      },
    });
  }, [
    personality,
    customSystemMessage,
    reasoningEffort,
    llm,
    includedDocuments,
    excludedDocuments,
    toolsSpec,
    regenerate,
  ]);

  const handleRetry = useCallback(async () => {
    const lastUserMessage = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUserMessage) return;
    const text = lastUserMessage.parts
      ?.filter((p): p is { type: "text"; text: string } => p.type === "text")
      .map((p) => p.text)
      .join("\n");
    if (!text) return;
    const authHeaders = await getAuthHeaders();
    await sendMessage(
      { text, messageId: lastUserMessage.id },
      {
        headers: authHeaders,
        body: {
          personality,
          system_message: personality === "custom" ? customSystemMessage : undefined,
          reasoning_effort: reasoningEffort,
          llm: buildLlmConfig(llm),
          included_documents: includedDocuments,
          excluded_documents: excludedDocuments,
          tools: buildToolsPayload(toolsSpec),
        },
      },
    );
  }, [
    messages,
    personality,
    customSystemMessage,
    reasoningEffort,
    llm,
    includedDocuments,
    excludedDocuments,
    toolsSpec,
    sendMessage,
  ]);

  const handleCompact = useCallback(
    async (retryMessageText?: string) => {
      setIsCompacting(true);
      try {
        const result = await compactConversation(
          id,
          buildLlmConfig({
            model: smallModel || llm.model,
            apiKey: llm.apiKey,
            baseUrl: llm.baseUrl,
          }),
        );
        clearAll();
        if (retryMessageText) {
          pendingRetryRef.current = retryMessageText;
        }
        await navigate({
          to: "/conversations/$id",
          params: { id: result.new_conversation_id },
        });
      } catch (err) {
        console.error("Compaction failed:", err);
      } finally {
        setIsCompacting(false);
      }
    },
    [id, llm, smallModel, clearAll, navigate],
  );

  // Auto-compact when context window is exceeded
  useEffect(() => {
    if (!isContextLengthError(error)) return;
    void handleCompact(getLastUserMessageText(messages));
  }, [error, messages, handleCompact]);

  // Sync tool outputs to the document store
  useEffect(() => {
    for (const message of messages) {
      if (!message.parts) continue;
      for (const part of message.parts) {
        const info = getToolPartInfo(part);
        if (!info || info.state !== "output-available") continue;
        processToolOutput(info.toolName, info.input, info.output, addChunk, markFullDocument);
      }
    }
  }, [messages, addChunk, markFullDocument]);

  return (
    <Tabs value={activeTab} onValueChange={setActiveTab} className="flex h-full flex-col">
      <div className="shrink-0 border-b px-4 flex items-center justify-between h-15">
        <TabsList>
          <TabsTrigger value="chat">
            <MessageSquareIcon className="h-4 w-4 mr-1.5" />
            Chat
          </TabsTrigger>
          <TabsTrigger value="history" onClick={() => fetchConversations()}>
            <HistoryIcon className="h-4 w-4 mr-1.5" />
            History
          </TabsTrigger>
        </TabsList>
        <div className="flex items-center gap-1">
          {messages.length > 0 && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => handleCompact()}
              disabled={status !== "ready" || isCompacting}
              title="Compact conversation"
            >
              <Minimize2 className="h-4 w-4" />
            </Button>
          )}
          <Button variant="ghost" size="icon" onClick={handleNewChat} title="New chat">
            <SquarePen className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <TabsContent value="chat" className="flex min-h-0 flex-1 flex-col">
        <Conversation className="min-h-0 flex-1">
          <ConversationContent className="gap-4">
            {compactedFrom && (
              <Alert>
                <HistoryIcon className="h-4 w-4" />
                <AlertTitle>Continued conversation</AlertTitle>
                <AlertDescription>
                  <p>
                    This conversation was compacted from a{" "}
                    <button
                      type="button"
                      onClick={() => {
                        clearAll();
                        void navigate({
                          to: "/conversations/$id",
                          params: { id: compactedFrom },
                        });
                      }}
                      className="underline hover:text-primary"
                    >
                      previous chat
                    </button>
                    .
                  </p>
                </AlertDescription>
              </Alert>
            )}
            {isCompacting && (
              <Alert>
                <Minimize2 className="h-4 w-4" />
                <AlertTitle>Compacting conversation</AlertTitle>
                <AlertDescription>
                  Summarizing the conversation to fit within context limits...
                </AlertDescription>
              </Alert>
            )}
            {isLoadingHistory && <Loader />}
            {!isLoadingHistory && conversationError && (
              <div className="flex flex-col items-center justify-center gap-4 py-12 text-center">
                <AlertCircle className="h-10 w-10 text-muted-foreground" />
                <div>
                  <p className="text-lg font-medium">Conversation not found</p>
                  <p className="text-sm text-muted-foreground">
                    This conversation does not exist or has been deleted.
                  </p>
                </div>
                <Button onClick={handleNewChat}>Start New Chat</Button>
              </div>
            )}
            {!isLoadingHistory && !conversationError && messages.length === 0 && !error && (
              <ConversationEmptyState
                title="Ask about your documents"
                description="Start a conversation to search and explore your documents."
              />
            )}
            {messages.map((message, messageIndex) => {
              const isLastMessage = messageIndex === messages.length - 1;
              const isAssistant = message.role === "assistant";
              const showActions = isAssistant && isLastMessage && status === "ready";

              const isUser = message.role === "user";
              const canEdit =
                isUser && (status === "ready" || status === "error") && editingMessageId !== message.id;

              return (
                <Message key={message.id} from={message.role}>
                  <MessageContent>
                    {message.parts?.map((part, partIndex) => {
                      const isLastTextPart =
                        part.type === "text" &&
                        isAssistant &&
                        isLastMessage &&
                        !message.parts?.slice(partIndex + 1).some((p) => p.type === "text");

                      return (
                        <MessagePart
                          key={partIndex}
                          part={part}
                          partIndex={partIndex}
                          isLastTextPart={isLastTextPart}
                          showActions={showActions}
                          isUserMessage={isUser}
                          messageId={message.id}
                          isEditing={editingMessageId === message.id}
                          onCancelEdit={() => setEditingMessageId(null)}
                          onSubmitEdit={handleEditMessage}
                          onRegenerate={handleRegenerate}
                          onApprove={(approvalId) =>
                            addToolApprovalResponse({
                              id: approvalId,
                              approved: true,
                            })
                          }
                          onDeny={(approvalId) =>
                            addToolApprovalResponse({
                              id: approvalId,
                              approved: false,
                            })
                          }
                        />
                      );
                    })}
                  </MessageContent>
                  {canEdit && (
                    <MessageActions className="ml-auto">
                      <MessageAction onClick={() => setEditingMessageId(message.id)} label="Edit">
                        <PencilIcon className="size-3" />
                      </MessageAction>
                      <MessageAction
                        onClick={() => {
                          const text = message.parts
                            ?.filter((p): p is { type: "text"; text: string } => p.type === "text")
                            .map((p) => p.text)
                            .join("\n");
                          if (text) void navigator.clipboard.writeText(text);
                        }}
                        label="Copy"
                      >
                        <CopyIcon className="size-3" />
                      </MessageAction>
                    </MessageActions>
                  )}
                </Message>
              );
            })}
            {status === "submitted" && <Loader />}
            {error && !isContextLengthError(error) && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>Error</AlertTitle>
                <AlertDescription className="flex items-start justify-between gap-2">
                  <span>{error.message || "An error occurred while processing your request."}</span>
                  <span className="flex shrink-0 gap-1">
                    <Button variant="outline" size="sm" onClick={handleRetry}>
                      <RefreshCcwIcon className="mr-1 h-3 w-3" />
                      Retry
                    </Button>
                    <Button variant="ghost" size="icon-sm" onClick={clearError}>
                      <X className="h-3 w-3" />
                      <span className="sr-only">Dismiss</span>
                    </Button>
                  </span>
                </AlertDescription>
              </Alert>
            )}
          </ConversationContent>
          <ConversationScrollButton />
        </Conversation>

        <div className="border-t p-4 space-y-3">
          {messages.length === 0 && (
            <Suggestions className="flex-wrap">
              {SUGGESTED_QUESTIONS.map((question) => (
                <Suggestion
                  key={question}
                  suggestion={question}
                  onClick={(q) => handleSendMessage(q)}
                />
              ))}
            </Suggestions>
          )}
          <PromptInput
            onSubmit={(msg) => {
              void handleSendMessage(msg.text, msg.files);
            }}
          >
            {hasDocumentFilters && (
              <PromptInputHeader>
                {includedDocuments.map((entry) => {
                  const isDir = entry.endsWith("/");
                  const displayName = isDir
                    ? (entry.slice(0, -1).split("/").pop() ?? entry)
                    : (entry.split("/").pop() ?? entry);
                  const Icon = isDir ? Folder : FileText;
                  return (
                    <Badge
                      key={`inc-${entry}`}
                      variant="secondary"
                      className="gap-1 text-xs"
                      title={entry}
                    >
                      <Icon className="h-3 w-3" />
                      {displayName}
                      <button
                        type="button"
                        className="ml-0.5 rounded-full hover:bg-muted"
                        onClick={() => onRemoveDocument(entry)}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </Badge>
                  );
                })}
                {excludedDocuments.map((entry) => {
                  const isDir = entry.endsWith("/");
                  const displayName = isDir
                    ? (entry.slice(0, -1).split("/").pop() ?? entry)
                    : (entry.split("/").pop() ?? entry);
                  const Icon = isDir ? Folder : EyeOff;
                  return (
                    <Badge
                      key={`exc-${entry}`}
                      variant="destructive"
                      className="gap-1 text-xs"
                      title={entry}
                    >
                      <Icon className="h-3 w-3" />
                      {displayName}
                      <button
                        type="button"
                        className="ml-0.5 rounded-full hover:bg-muted"
                        onClick={() => onRemoveDocument(entry)}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </Badge>
                  );
                })}
              </PromptInputHeader>
            )}
            <AttachedFiles />
            <PromptInputBody>
              <PromptInputTextarea
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                placeholder="Ask about your documents..."
              />
            </PromptInputBody>
            <PromptInputFooter>
              <PromptInputTools>
                <FileSelectButton />
                <SpeechInput
                  variant="ghost"
                  size="icon-sm"
                  disabled={status !== "ready"}
                  onTranscriptionChange={handleTranscriptionChange}
                />
                <PromptInputSelect
                  value={reasoningEffort}
                  onValueChange={(v) => setReasoningEffort(v as ReasoningEffort)}
                >
                  <PromptInputSelectTrigger className="h-8 w-auto min-w-20">
                    <BrainIcon className="h-4 w-4" />
                    <PromptInputSelectValue placeholder="Effort" />
                  </PromptInputSelectTrigger>
                  <PromptInputSelectContent>
                    {REASONING_EFFORT_OPTIONS.map((option) => (
                      <PromptInputSelectItem key={option.value} value={option.value}>
                        {option.label}
                      </PromptInputSelectItem>
                    ))}
                  </PromptInputSelectContent>
                </PromptInputSelect>
                <SettingsDialog />
              </PromptInputTools>
              <PromptInputSubmit status={status} onStop={stop} />
            </PromptInputFooter>
          </PromptInput>
        </div>
      </TabsContent>

      <TabsContent value="history" className="min-h-0 flex-1">
        <ConversationsList
          currentConversationId={id}
          onConversationSelect={handleConversationSelect}
        />
      </TabsContent>
    </Tabs>
  );
}
