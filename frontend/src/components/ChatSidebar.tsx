import { useChat, type UIMessage } from '@ai-sdk/react';
import { DefaultChatTransport } from 'ai';
import { AlertCircle, BotIcon, CopyIcon, HistoryIcon, MessageSquareIcon, RefreshCcwIcon, SparklesIcon, SquarePen } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import {
  API_BASE_URL,
  chatConfigToHeaders,
  createConversation,
  getAuthHeaders,
  getConversationDocumentReferences,
  getMessages,
} from '../lib/api';
import {
  PERSONALITY_OPTIONS,
  type GrepMatch,
  type Personality,
  type RetrievedDocument,
  type SearchDocumentsInput,
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
import { Alert, AlertDescription, AlertTitle } from './ui/alert';

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
  addSearchResults: (results: RetrievedDocument[], query: string) => void,
  addDocument: (filename: string, content: string, source: string) => void
) {
  if (!input || !output) return;

  switch (toolName) {
    case 'search_documents': {
      const results = output as RetrievedDocument[];
      const query = input.query as string;
      if (results && query) addSearchResults(results, query);
      break;
    }
    case 'get_document': {
      const filename = input.filename as string;
      if (filename && typeof output === 'string') {
        addDocument(filename, output, 'get_document');
      }
      break;
    }
    case 'get_document_range': {
      const filename = input.filename as string;
      const result = output as { content?: string };
      if (filename && result?.content) {
        addDocument(filename, result.content, `lines ${input.start_line}-${input.end_line}`);
      }
      break;
    }
    case 'get_context': {
      const filename = input.filename as string;
      const result = output as { content?: string };
      if (filename && result?.content) {
        addDocument(filename, result.content, `context around line ${input.line}`);
      }
      break;
    }
    case 'grep_document': {
      const filename = input.filename as string;
      const matches = output as GrepMatch[];
      if (filename && matches?.length) {
        const content = matches.map((m) => `${m.line}: ${m.content ?? ''}`).join('\n');
        addDocument(filename, content, `grep: ${input.pattern}`);
      }
      break;
    }
    case 'grep_documents': {
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
  }
}

// --- Tool display components ---

interface ToolPartDisplayProps {
  toolName: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  part: any;
}

/** Renders search_documents tool with custom formatting. */
function SearchDocumentsToolDisplay({ part }: Omit<ToolPartDisplayProps, 'toolName'>) {
  const state = part.state ?? 'output-available';
  const input = parseJson<SearchDocumentsInput>(part.input);
  const output = parseJson<RetrievedDocument[]>(part.output);

  return (
    <Tool defaultOpen={state !== 'output-available'}>
      <ToolHeader title="Document Search" type="tool-search_documents" state={state} />
      <ToolContent>
        {input?.query && (
          <ToolSection title="Parameters">
            <ToolKeyValue label="Query" value={`"${input.query}"`} />
            {input.top_k && <ToolKeyValue label="Max results" value={input.top_k} />}
          </ToolSection>
        )}
        {output && (
          <ToolResult>
            <ToolKeyValue label="Found" value={`${output.length} document(s)`} />
            {output.map((d) => (
              <ToolKeyValue
                key={d.filename}
                label={d.filename}
                value={`${(d.score * 100).toFixed(0)}% match`}
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
    <Tool defaultOpen={state !== 'output-available'}>
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
  if (toolName === 'search_documents') {
    return <SearchDocumentsToolDisplay part={part} />;
  }
  return <GenericToolDisplay toolName={toolName} part={part} />;
}

// --- Text part component ---

interface TextPartDisplayProps {
  text: string;
  showActions: boolean;
  onRegenerate: () => void;
}

function TextPartDisplay({ text, showActions, onRegenerate }: TextPartDisplayProps) {
  return (
    <div>
      <MessageResponse>{text}</MessageResponse>
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

  const toolName = getToolName(part as { type: string; toolName?: string });
  if (toolName) {
    return <ToolPartDisplay key={partIndex} toolName={toolName} part={part} />;
  }

  return null;
}


interface ChatSidebarProps {
  id: string;
  pendingContent?: string | null;
  onPendingContentConsumed?: () => void;
}

const SUGGESTED_QUESTIONS = [
  'What documents do I have?',
  'Summarize my most recent notes',
  'Find documents about meetings',
  'What are my action items?',
];

export function ChatSidebar({ id, pendingContent, onPendingContentConsumed }: ChatSidebarProps) {
  const navigate = useNavigate();
  const addSearchResults = useFetchedDocumentsStore((state) => state.addSearchResults);
  const addDocument = useFetchedDocumentsStore((state) => state.addDocument);
  const addDocumentReference = useFetchedDocumentsStore((state) => state.addDocumentReference);
  const clearDocuments = useFetchedDocumentsStore((state) => state.clearDocuments);
  const fetchConversations = useConversationsStore((state) => state.fetchConversations);
  const { llm, availableModels, setLLM } = useSettingsStore();
  const [inputValue, setInputValue] = useState('');
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [activeTab, setActiveTab] = useState('chat');
  const [personality, setPersonality] = useState<Personality>('default');

  useEffect(() => {
    if (pendingContent) {
      setInputValue((prev) => prev ? `${prev}\n\n${pendingContent}` : pendingContent);
      onPendingContentConsumed?.();
    }
  }, [pendingContent, onPendingContentConsumed]);

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

    // Load document references for the selected conversation
    const refs = await getConversationDocumentReferences(conversationId);
    for (const ref of refs) {
      addDocumentReference(ref.filename, ref.sources, ref.score);
    }
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
    getMessages(id)
      .then(async (initialMessages) => {
        if (!cancelled && initialMessages.length > 0) {
          setMessages(initialMessages);
        }
        // Also load document references for the current conversation
        if (!cancelled) {
          const refs = await getConversationDocumentReferences(id);
          for (const ref of refs) {
            addDocumentReference(ref.filename, ref.sources, ref.score);
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

  const handleSendMessage = async (text: string) => {
    if (!text.trim()) return;
    const authHeaders = await getAuthHeaders();
    sendMessage(
      { text },
      {
        headers: {
          ...authHeaders,
          ...chatConfigToHeaders({
            conversationId: id,
            model: llm.model,
            apiKey: llm.apiKey,
            baseUrl: llm.baseUrl,
            personality,
          }),
        },
      }
    );
    setInputValue('');
  };

  // Sync tool outputs to the document store
  useEffect(() => {
    for (const message of messages) {
      if (!message.parts) continue;
      for (const part of message.parts) {
        const state = 'state' in part ? part.state : 'output-available';
        if (state !== 'output-available') continue;

        const toolName = getToolName(part as { type: string; toolName?: string });
        if (!toolName) continue;

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const toolPart = part as any;
        const input = parseJson<Record<string, unknown>>(toolPart.input);
        const output = parseJson<unknown>(toolPart.output);

        processToolOutput(toolName, input, output, addSearchResults, addDocument);
      }
    }
  }, [messages, addSearchResults, addDocument]);

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
            {error && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>Error</AlertTitle>
                <AlertDescription>
                  {error.message || 'An error occurred while processing your request.'}
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
                          onRegenerate={regenerate}
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
                    <BotIcon className="h-3 w-3 text-muted-foreground" />
                    <span className="text-xs text-muted-foreground">Model</span>
                  </div>
                  <PromptInputSelect value={llm.model} onValueChange={(model) => setLLM({ model })}>
                    <PromptInputSelectTrigger className="h-8 w-auto min-w-30">
                      <PromptInputSelectValue placeholder="Select model" />
                    </PromptInputSelectTrigger>
                    <PromptInputSelectContent>
                      {availableModels.map((model) => (
                        <PromptInputSelectItem key={model.value} value={model.value}>
                          {model.name}
                        </PromptInputSelectItem>
                      ))}
                    </PromptInputSelectContent>
                  </PromptInputSelect>
                </div>
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
