import { useChat, type UIMessage } from '@ai-sdk/react';
import { DefaultChatTransport } from 'ai';
import { AlertCircle, CopyIcon, EyeOff, FileText, Folder, HistoryIcon, Minimize2, MessageSquareIcon, RefreshCcwIcon, SparklesIcon, SquarePen, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Components } from 'streamdown';
import { useNavigate } from '@tanstack/react-router';
import {
  API_BASE_URL,
  buildLlmConfig,
  compactConversation,
  createConversation,
  getAuthHeaders,
  getConversation,
  getConversationDocumentReferences,
  getMessages,
} from '../lib/api';
import {
  PERSONALITY_OPTIONS,
  type DocumentRange,
  type GrepMatch,
  type Personality,
  type RetrievedChunk,
} from '../lib/types';
import { useConversationsStore } from '../stores/conversations-store';
import { useFetchedDocumentsStore } from '../stores/fetched-documents-store';
import { useSettingsStore } from '../stores/settings-store';
import { ConversationsList } from './ConversationsList';
import { Button } from './ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from './ai-elements/conversation';
import { Loader } from './ai-elements/loader';
import {
  Message,
  MessageAction,
  MessageActions,
  MessageContent,
  MessageResponse,
} from './ai-elements/message';
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
} from './ai-elements/prompt-input';
import { Suggestion, Suggestions } from './ai-elements/suggestion';
import { Tool, ToolContent, ToolHeader } from './ai-elements/tool';
import { SettingsDialog } from './SettingsDialog';
import {
  ToolError,
  ToolKeyValue,
  ToolParameters,
  ToolResult,
  ToolSection,
} from './ToolDisplay';
import { Citation } from './Citation';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import { Badge } from './ui/badge';

// --- Helper functions ---

/**
 * Extract tool name from both static and dynamic formats.
 *
 * Pydantic AI's VercelAIAdapter produces different formats depending on the code path:
 * - Streaming (dispatch_request): emits ToolOutputAvailableChunk without dynamic=true,
 *   causing the frontend SDK to create static format parts (type: "tool-{name}")
 * - History (dump_messages): correctly creates DynamicToolOutputAvailablePart
 *   with the dynamic format (type: "dynamic-tool", toolName: "{name}")
 *
 * This inconsistency is a bug in pydantic-ai's _event_stream.py.
 */
function getToolName(part: { type: string; toolName?: string }): string | null {
  if (part.type === 'dynamic-tool' && part.toolName) return part.toolName;
  if (part.type.startsWith('tool-')) return part.type.replace('tool-', '');
  return null;
}

/** Parse JSON string or return value as-is. */
function parseJson<T>(value: unknown): T | undefined {
  if (typeof value === 'string') {
    try {
      return JSON.parse(value) as T;
    } catch {
      return undefined;
    }
  }
  return value as T;
}

/** Process tool output and add to document store. */
function processToolOutput(
  toolName: string,
  input: Record<string, unknown> | undefined,
  output: unknown,
  addDocument: (filename: string, content: string, source: string) => void
) {
  if (!input || output == null) return;

  switch (toolName) {
    case 'semantic_search': {
      const chunks = output as RetrievedChunk[];
      if (chunks?.length) {
        const query = input.query as string;
        for (const chunk of chunks) {
          addDocument(chunk.filename, chunk.text, `search${query ? `: ${query}` : ''}`);
        }
      }
      break;
    }
    case 'get_document': {
      const filename = input.filename as string;
      const content = typeof output === 'string' ? output : null;
      if (filename && content) {
        addDocument(filename, content, 'get_document');
      }
      break;
    }
    case 'get_document_lines': {
      const filename = input.filename as string;
      const result = output as DocumentRange;
      if (filename && result?.content) {
        addDocument(filename, result.content, `lines ${result.start_line}-${result.end_line}`);
      }
      break;
    }
    case 'grep': {
      const matches = output as GrepMatch[];
      const pattern = input.pattern as string;
      if (matches?.length && pattern) {
        const byFile = new Map<string, GrepMatch[]>();
        for (const match of matches) {
          if (match.line > 0) {
            const fileMatches = byFile.get(match.filename) ?? [];
            fileMatches.push(match);
            byFile.set(match.filename, fileMatches);
          }
        }
        for (const [filename, fileMatches] of byFile) {
          const content = fileMatches.map((m) => `${m.line}: ${m.content ?? ''}`).join('\n');
          addDocument(filename, content, `grep: ${pattern}`);
        }
      }
      break;
    }
    case 'get_chunk': {
      const filename = input.filename as string;
      const chunkIndex = input.chunk_index as number;
      if (filename && typeof output === 'string') {
        addDocument(filename, output, `chunk ${chunkIndex}`);
      }
      break;
    }
  }
}

/** Typed info extracted from a tool message part. */
interface ToolPartInfo {
  toolName: string;
  state: string;
  input: Record<string, unknown> | undefined;
  output: unknown;
}

/** Extract tool info from a message part, handling both streaming and history formats. */
function getToolPartInfo(part: UIMessage['parts'][number]): ToolPartInfo | null {
  const typed = part as { type: string; toolName?: string; state?: string; input?: unknown; output?: unknown };
  const toolName = getToolName(typed);
  if (!toolName) return null;
  return {
    toolName,
    state: typed.state ?? 'output-available',
    input: parseJson<Record<string, unknown>>(typed.input),
    output: parseJson<unknown>(typed.output) ?? typed.output,
  };
}

/** Extract text from the last user message. */
function getLastUserMessageText(messages: UIMessage[]): string | undefined {
  const lastUserMessage = [...messages].reverse().find((m) => m.role === 'user');
  if (!lastUserMessage?.parts) return undefined;
  const texts = lastUserMessage.parts
    .filter((p): p is { type: 'text'; text: string } => p.type === 'text')
    .map((p) => p.text);
  return texts.length > 0 ? texts.join('\n') : undefined;
}

/** Check if an error is a context length exceeded error. */
function isContextLengthError(error: Error | null | undefined): boolean {
  if (!error) return false;
  const msg = error.message || '';
  return msg.includes('context_length_exceeded') || msg.includes('maximum context length');
}

/** Load document references for a conversation and add them to the store. */
async function loadDocumentReferences(
  conversationId: string,
  addRef: (filename: string, sources: string[], score?: number) => void,
): Promise<void> {
  const refs = await getConversationDocumentReferences(conversationId);
  for (const ref of refs) {
    addRef(ref.filename, ref.sources, ref.score);
  }
}

// --- Tool display components ---

interface ToolPartDisplayProps {
  toolName: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  part: any;
}

/** Renders semantic_search tool with custom formatting. */
function SearchToolDisplay({ toolName, part }: ToolPartDisplayProps) {
  const state = part.state ?? 'output-available';
  const input = parseJson<{ query: string; type?: string; top_k?: number }>(part.input);
  const output = parseJson<RetrievedChunk[]>(part.output);
  const title = input?.type === 'sparse' ? 'Keyword Search' : 'Semantic Search';

  return (
    <Tool defaultOpen={false}>
      <ToolHeader title={title} type={`tool-${toolName}`} state={state} />
      <ToolContent>
        {input?.query && (
          <ToolSection title="Parameters">
            <ToolKeyValue label="Query" value={`"${input.query}"`} />
            {input.top_k && <ToolKeyValue label="Max results" value={input.top_k} />}
          </ToolSection>
        )}
        {output && (
          <ToolResult>
            <ToolKeyValue label="Found" value={`${output.length} chunk(s)`} />
            {output.map((c) => (
              <ToolKeyValue
                key={`${c.filename}::${c.chunk_index}`}
                label={`${c.filename} #${c.chunk_index}`}
                value={`${(c.score * 100).toFixed(0)}% match`}
                indent
              />
            ))}
          </ToolResult>
        )}
        {part.errorText && <ToolError message={part.errorText} />}
      </ToolContent>
    </Tool>
  );
}

/** Renders any tool with generic parameter/result display. */
function GenericToolDisplay({ toolName, part }: ToolPartDisplayProps) {
  const state = part.state ?? 'output-available';
  const input = parseJson<Record<string, unknown>>(part.input);

  return (
    <Tool defaultOpen={false}>
      <ToolHeader type={`tool-${toolName}`} state={state} />
      <ToolContent>
        {input && <ToolParameters params={input} />}
        {part.output !== undefined && (
          <ToolResult>
            <pre className="whitespace-pre-wrap">
              {typeof part.output === 'string' ? part.output : JSON.stringify(part.output, null, 2)}
            </pre>
          </ToolResult>
        )}
        {state === 'output-error' && part.errorText && <ToolError message={part.errorText} />}
      </ToolContent>
    </Tool>
  );
}

/** Renders a tool part based on tool name. */
function ToolPartDisplay({ toolName, part }: ToolPartDisplayProps) {
  if (toolName === 'semantic_search') {
    return <SearchToolDisplay toolName={toolName} part={part} />;
  }
  return <GenericToolDisplay toolName={toolName} part={part} />;
}

// --- Text part component ---

interface TextPartDisplayProps {
  text: string;
  showActions: boolean;
  onRegenerate: () => void;
}

const CITATION_ALLOWED_TAGS = { cite: ['filename', 'chunk'] };
const CITATION_COMPONENTS = { cite: Citation } as Components;

function TextPartDisplay({ text, showActions, onRegenerate }: TextPartDisplayProps) {
  return (
    <div>
      <MessageResponse allowedTags={CITATION_ALLOWED_TAGS} components={CITATION_COMPONENTS}>{text}</MessageResponse>
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

// --- Message part renderer ---

interface MessagePartProps {
  part: UIMessage['parts'][number];
  partIndex: number;
  isLastTextPart: boolean;
  showActions: boolean;
  onRegenerate: () => void;
}

function MessagePart({ part, partIndex, isLastTextPart, showActions, onRegenerate }: MessagePartProps) {
  if (part.type === 'text') {
    return (
      <TextPartDisplay
        key={partIndex}
        text={part.text}
        showActions={isLastTextPart && showActions}
        onRegenerate={onRegenerate}
      />
    );
  }

  const info = getToolPartInfo(part);
  if (info) {
    return <ToolPartDisplay key={partIndex} toolName={info.toolName} part={part} />;
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
  'What documents do I have?',
  'Summarize my most recent notes',
  'Find documents about meetings',
  'What are my action items?',
];

export function ChatSidebar({ id, includedDocuments, excludedDocuments, onRemoveDocument, onClearDocuments }: ChatSidebarProps) {
  const navigate = useNavigate();
  const addDocument = useFetchedDocumentsStore((state) => state.addDocument);
  const addDocumentReference = useFetchedDocumentsStore((state) => state.addDocumentReference);
  const clearDocuments = useFetchedDocumentsStore((state) => state.clearDocuments);
  const fetchConversations = useConversationsStore((state) => state.fetchConversations);
  const { llm } = useSettingsStore();
  const [inputValue, setInputValue] = useState('');
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [activeTab, setActiveTab] = useState('chat');
  const [personality, setPersonality] = useState<Personality>('default');
  const [compactedFrom, setCompactedFrom] = useState<string | null>(null);
  const [isCompacting, setIsCompacting] = useState(false);
  const pendingRetryRef = useRef<string | null>(null);

  const hasDocumentFilters = includedDocuments.length > 0 || excludedDocuments.length > 0;

  const handleNewChat = async () => {
    const newId = await createConversation();
    clearDocuments();
    setActiveTab('chat');
    navigate({ to: '/chat/$id', params: { id: newId } });
  };

  const handleConversationSelect = async (conversationId: string) => {
    clearDocuments();
    setActiveTab('chat');
    navigate({ to: '/chat/$id', params: { id: conversationId } });
    await loadDocumentReferences(conversationId, addDocumentReference);
  };

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: `${API_BASE_URL}/api/chat`,
      }),
    []
  );

  const { messages, sendMessage, status, error, regenerate, stop, setMessages } = useChat({
    id,
    transport,
  });

  useEffect(() => {
    let cancelled = false;
    setIsLoadingHistory(true);
    setCompactedFrom(null);
    getMessages(id)
      .then(async (initialMessages) => {
        if (!cancelled && initialMessages.length > 0) {
          setMessages(initialMessages);
        }
        if (!cancelled) {
          await loadDocumentReferences(id, addDocumentReference);
        }
        if (!cancelled) {
          const conv = await getConversation(id);
          if (conv?.compacted_from) {
            setCompactedFrom(conv.compacted_from);
          }
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
  }, [id, setMessages, addDocumentReference]);

  const handleSendMessage = useCallback(async (text: string) => {
    if (!text.trim()) return;
    const authHeaders = await getAuthHeaders();
    sendMessage(
      { text },
      {
        headers: authHeaders,
        body: {
          conversation_id: id,
          personality,
          llm: buildLlmConfig(llm),
          included_documents: includedDocuments,
          excluded_documents: excludedDocuments,
        },
      }
    );
    setInputValue('');
    onClearDocuments();
  }, [id, personality, llm, includedDocuments, excludedDocuments, sendMessage, onClearDocuments]);

  // Re-send the pending message after navigating to a compacted conversation
  useEffect(() => {
    if (isLoadingHistory || !pendingRetryRef.current) return;
    const text = pendingRetryRef.current;
    pendingRetryRef.current = null;
    handleSendMessage(text);
  }, [isLoadingHistory, handleSendMessage]);

  const handleRegenerate = useCallback(async () => {
    const authHeaders = await getAuthHeaders();
    regenerate({
      headers: authHeaders,
      body: {
        conversation_id: id,
        personality,
        llm: buildLlmConfig(llm),
        included_documents: includedDocuments,
        excluded_documents: excludedDocuments,
      },
    });
  }, [id, personality, llm, includedDocuments, excludedDocuments, regenerate]);

  const handleCompact = useCallback(async (retryMessageText?: string) => {
    setIsCompacting(true);
    try {
      const result = await compactConversation(id);
      clearDocuments();
      if (retryMessageText) {
        pendingRetryRef.current = retryMessageText;
      }
      navigate({ to: '/chat/$id', params: { id: result.new_conversation_id } });
    } catch (err) {
      console.error('Compaction failed:', err);
    } finally {
      setIsCompacting(false);
    }
  }, [id, clearDocuments, navigate]);

  // Auto-compact when context window is exceeded
  useEffect(() => {
    if (!isContextLengthError(error)) return;
    handleCompact(getLastUserMessageText(messages));
  }, [error, messages, handleCompact]);

  // Sync tool outputs to the document store
  useEffect(() => {
    for (const message of messages) {
      if (!message.parts) continue;
      for (const part of message.parts) {
        const info = getToolPartInfo(part);
        if (!info || info.state !== 'output-available') continue;
        processToolOutput(info.toolName, info.input, info.output, addDocument);
      }
    }
  }, [messages, addDocument]);

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
        <Button variant="ghost" size="icon" onClick={handleNewChat} title="New chat">
          <SquarePen className="h-4 w-4" />
        </Button>
      </div>

      <TabsContent value="chat" className="flex min-h-0 flex-1 flex-col">
        <Conversation className="min-h-0 flex-1">
          <ConversationContent className="gap-4">
            {compactedFrom && (
              <Alert>
                <HistoryIcon className="h-4 w-4" />
                <AlertTitle>Continued conversation</AlertTitle>
                <AlertDescription>
                  This conversation was compacted from a{' '}
                  <button
                    onClick={() => {
                      clearDocuments();
                      navigate({ to: '/chat/$id', params: { id: compactedFrom } });
                    }}
                    className="underline hover:text-primary"
                  >
                    previous chat
                  </button>
                  .
                </AlertDescription>
              </Alert>
            )}
            {error && !isContextLengthError(error) && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>Error</AlertTitle>
                <AlertDescription>
                  {error.message || 'An error occurred while processing your request.'}
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
            {!isLoadingHistory && messages.length === 0 && !error && (
              <ConversationEmptyState
                title="Ask about your documents"
                description="Start a conversation to search and explore your documents."
              />
            )}
            {messages.map((message, messageIndex) => {
              const isLastMessage = messageIndex === messages.length - 1;
              const isAssistant = message.role === 'assistant';
              const showActions = isAssistant && isLastMessage && status === 'ready';

              return (
                <Message key={message.id} from={message.role}>
                  <MessageContent>
                    {message.parts?.map((part, partIndex) => {
                      const isLastTextPart =
                        part.type === 'text' &&
                        isAssistant &&
                        isLastMessage &&
                        !message.parts?.slice(partIndex + 1).some((p) => p.type === 'text');

                      return (
                        <MessagePart
                          key={partIndex}
                          part={part}
                          partIndex={partIndex}
                          isLastTextPart={isLastTextPart}
                          showActions={showActions}
                          onRegenerate={handleRegenerate}
                        />
                      );
                    })}
                  </MessageContent>
                </Message>
              );
            })}
            {status === 'submitted' && <Loader />}
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
              handleSendMessage(msg.text);
            }}
          >
            {hasDocumentFilters && (
              <PromptInputHeader>
                {includedDocuments.map((entry) => {
                  const isDir = entry.endsWith('/');
                  const displayName = isDir
                    ? entry.slice(0, -1).split('/').pop() ?? entry
                    : entry.split('/').pop() ?? entry;
                  const Icon = isDir ? Folder : FileText;
                  return (
                    <Badge key={`inc-${entry}`} variant="secondary" className="gap-1 text-xs" title={entry}>
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
                  const isDir = entry.endsWith('/');
                  const displayName = isDir
                    ? entry.slice(0, -1).split('/').pop() ?? entry
                    : entry.split('/').pop() ?? entry;
                  const Icon = isDir ? Folder : EyeOff;
                  return (
                    <Badge key={`exc-${entry}`} variant="destructive" className="gap-1 text-xs" title={entry}>
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
            <PromptInputBody>
              <PromptInputTextarea
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                placeholder="Ask about your documents..."
              />
            </PromptInputBody>
            <PromptInputFooter>
              <PromptInputTools>
                <SettingsDialog />
                <div className="flex flex-col items-center gap-1">
                  <div className="flex items-center gap-1">
                    <SparklesIcon className="h-3 w-3 text-muted-foreground" />
                    <span className="text-xs text-muted-foreground">Personality</span>
                  </div>
                  <PromptInputSelect value={personality} onValueChange={(value) => setPersonality(value as Personality)}>
                    <PromptInputSelectTrigger className="h-8 w-auto min-w-24">
                      <PromptInputSelectValue placeholder="Personality" />
                    </PromptInputSelectTrigger>
                    <PromptInputSelectContent>
                      {PERSONALITY_OPTIONS.map((option) => (
                        <PromptInputSelectItem key={option.value} value={option.value}>
                          {option.label}
                        </PromptInputSelectItem>
                      ))}
                    </PromptInputSelectContent>
                  </PromptInputSelect>
                </div>
                {messages.length > 0 && (
                  <div className="flex flex-col items-center gap-1">
                    <div className="flex items-center gap-1">
                      <Minimize2 className="h-3 w-3 text-muted-foreground" />
                      <span className="text-xs text-muted-foreground">Compact</span>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-8"
                      onClick={() => handleCompact()}
                      disabled={status !== 'ready' || isCompacting}
                      title="Summarize and continue in a new chat"
                    >
                      {isCompacting ? 'Compacting...' : 'Compact'}
                    </Button>
                  </div>
                )}
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
