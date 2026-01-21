import { useChat } from '@ai-sdk/react';
import { DefaultChatTransport, type ToolUIPart } from 'ai';
import { AlertCircle, CopyIcon, RefreshCcwIcon, SquarePen } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { API_BASE_URL, createConversation, getMessages } from '../lib/api';
import type {
  DocumentRange,
  GrepMatch,
  RetrievedDocument,
  SearchDocumentsInput,
} from '../lib/types';
import { useDocumentStore } from '../stores/document-store';
import { Button } from './ui/button';
import { useSettingsStore } from '../stores/settings-store';
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

type SearchDocumentsToolUIPart = ToolUIPart<{
  search_documents: {
    input: SearchDocumentsInput;
    output: RetrievedDocument[];
  };
}>;

type GetDocumentToolUIPart = ToolUIPart<{
  get_document: {
    input: { filename: string };
    output: string | null;
  };
}>;

type GetDocumentRangeToolUIPart = ToolUIPart<{
  get_document_range: {
    input: { filename: string; start_line: number; end_line: number };
    output: DocumentRange | null;
  };
}>;

type GetContextToolUIPart = ToolUIPart<{
  get_context: {
    input: { filename: string; line: number; context?: number };
    output: DocumentRange | null;
  };
}>;

type GrepDocumentToolUIPart = ToolUIPart<{
  grep_document: {
    input: { filename: string; pattern: string };
    output: GrepMatch[];
  };
}>;

type GrepDocumentsToolUIPart = ToolUIPart<{
  grep_documents: {
    input: { pattern: string; include_content?: boolean };
    output: GrepMatch[];
  };
}>;

interface ChatSidebarProps {
  id: string;
}

const SUGGESTED_QUESTIONS = [
  'What documents do I have?',
  'Summarize my most recent notes',
  'Find documents about meetings',
  'What are my action items?',
];

export function ChatSidebar({ id }: ChatSidebarProps) {
  const navigate = useNavigate();
  const addSearchResults = useDocumentStore((state) => state.addSearchResults);
  const addDocument = useDocumentStore((state) => state.addDocument);
  const clearDocuments = useDocumentStore((state) => state.clearDocuments);
  const { llm, availableModels, setLLM } = useSettingsStore();
  const [inputValue, setInputValue] = useState('');
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);

  const handleNewChat = async () => {
    const newId = await createConversation();
    clearDocuments();
    navigate({ to: '/chat/$id', params: { id: newId } });
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
      .then((initialMessages) => {
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

  const handleSendMessage = (text: string) => {
    if (!text.trim()) return;
    sendMessage(
      { text },
      {
        body: {
          conversationId: id,
          model: llm.model,
          apiKey: llm.apiKey,
          baseUrl: llm.baseUrl,
        },
      }
    );
    setInputValue('');
  };

  useEffect(() => {
    for (const message of messages) {
      if (message.parts) {
        for (const part of message.parts) {
          if (!('state' in part) || part.state !== 'output-available') continue;

          if (part.type === 'tool-search_documents') {
            const toolPart = part as SearchDocumentsToolUIPart;
            if (toolPart.output && toolPart.input) {
              addSearchResults(toolPart.output, toolPart.input.query);
            }
          } else if (part.type === 'tool-get_document') {
            const toolPart = part as GetDocumentToolUIPart;
            if (toolPart.output && toolPart.input) {
              addDocument(toolPart.input.filename, toolPart.output, 'get_document');
            }
          } else if (part.type === 'tool-get_document_range') {
            const toolPart = part as GetDocumentRangeToolUIPart;
            if (toolPart.output && toolPart.input) {
              const { filename, start_line, end_line } = toolPart.input;
              addDocument(filename, toolPart.output.content, `lines ${start_line}-${end_line}`);
            }
          } else if (part.type === 'tool-get_context') {
            const toolPart = part as GetContextToolUIPart;
            if (toolPart.output && toolPart.input) {
              const { filename, line } = toolPart.input;
              addDocument(filename, toolPart.output.content, `context around line ${line}`);
            }
          } else if (part.type === 'tool-grep_document') {
            const toolPart = part as GrepDocumentToolUIPart;
            if (toolPart.output?.length && toolPart.input) {
              const content = toolPart.output.map((m) => `${m.line_number}: ${m.line}`).join('\n');
              addDocument(toolPart.input.filename, content, `grep: ${toolPart.input.pattern}`);
            }
          } else if (part.type === 'tool-grep_documents') {
            const toolPart = part as GrepDocumentsToolUIPart;
            if (toolPart.output?.length && toolPart.input) {
              const byFile = new Map<string, GrepMatch[]>();
              for (const match of toolPart.output) {
                if (match.line_number > 0) {
                  const matches = byFile.get(match.filename) ?? [];
                  matches.push(match);
                  byFile.set(match.filename, matches);
                }
              }
              for (const [filename, matches] of byFile) {
                const content = matches.map((m) => `${m.line_number}: ${m.line}`).join('\n');
                addDocument(filename, content, `grep: ${toolPart.input.pattern}`);
              }
            }
          }
        }
      }
    }
  }, [messages, addSearchResults, addDocument]);

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b px-4 flex items-center justify-between h-[60px]">
        <h2 className="font-semibold">Chat</h2>
        <Button variant="ghost" size="icon" onClick={handleNewChat} title="New chat">
          <SquarePen className="h-4 w-4" />
        </Button>
      </div>

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

            return (
              <Message key={message.id} from={message.role}>
                <MessageContent>
                  {message.parts?.map((part, partIndex) => {
                    if (part.type === 'text') {
                      const isLastTextPart =
                        isAssistant &&
                        isLastMessage &&
                        !message.parts?.slice(partIndex + 1).some((p) => p.type === 'text');

                      return (
                        <div key={partIndex}>
                          <MessageResponse>{part.text}</MessageResponse>
                          {isLastTextPart && status === 'ready' && (
                            <MessageActions>
                              <MessageAction
                                onClick={() => regenerate()}
                                label="Retry"
                              >
                                <RefreshCcwIcon className="size-3" />
                              </MessageAction>
                              <MessageAction
                                onClick={() => navigator.clipboard.writeText(part.text)}
                                label="Copy"
                              >
                                <CopyIcon className="size-3" />
                              </MessageAction>
                            </MessageActions>
                          )}
                        </div>
                      );
                    }
                    if (part.type === 'tool-search_documents' && 'state' in part) {
                      const toolPart = part as SearchDocumentsToolUIPart;
                      return (
                        <Tool
                          key={partIndex}
                          defaultOpen={toolPart.state !== 'output-available'}
                        >
                          <ToolHeader
                            title="Document Search"
                            type={toolPart.type}
                            state={toolPart.state}
                          />
                          <ToolContent>
                            {toolPart.input && (
                              <ToolSection title="Parameters">
                                <ToolKeyValue label="Query" value={`"${toolPart.input.query}"`} />
                                {toolPart.input.top_k && (
                                  <ToolKeyValue label="Max results" value={toolPart.input.top_k} />
                                )}
                              </ToolSection>
                            )}
                            {toolPart.state === 'output-available' && toolPart.output && (
                              <ToolResult>
                                <ToolKeyValue
                                  label="Found"
                                  value={`${toolPart.output.length} document(s)`}
                                />
                                {toolPart.output.map((d) => (
                                  <ToolKeyValue
                                    key={d.filename}
                                    label={d.filename}
                                    value={`${(d.score * 100).toFixed(0)}% match`}
                                    indent
                                  />
                                ))}
                              </ToolResult>
                            )}
                            {toolPart.errorText && <ToolError message={toolPart.errorText} />}
                          </ToolContent>
                        </Tool>
                      );
                    }
                    if (part.type.startsWith('tool-') && 'state' in part) {
                      const toolPart = part as ToolUIPart;
                      const inputObj = toolPart.input as Record<string, unknown> | undefined;
                      return (
                        <Tool
                          key={partIndex}
                          defaultOpen={toolPart.state === 'output-available'}
                        >
                          <ToolHeader type={toolPart.type} state={toolPart.state} />
                          <ToolContent>
                            {inputObj && <ToolParameters params={inputObj} />}
                            {toolPart.state === 'output-available' &&
                              toolPart.output !== undefined && (
                                <ToolResult>
                                  <pre className="whitespace-pre-wrap">
                                    {typeof toolPart.output === 'string'
                                      ? toolPart.output
                                      : JSON.stringify(toolPart.output, null, 2)}
                                  </pre>
                                </ToolResult>
                              )}
                            {toolPart.state === 'output-error' && toolPart.errorText && (
                              <ToolError message={toolPart.errorText} />
                            )}
                          </ToolContent>
                        </Tool>
                      );
                    }
                    return null;
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
              <PromptInputSelect value={llm.model} onValueChange={(model) => setLLM({ model })}>
                <PromptInputSelectTrigger className="h-8 w-auto min-w-[120px]">
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
            </PromptInputTools>
            <PromptInputSubmit status={status} onStop={stop} />
          </PromptInputFooter>
        </PromptInput>
      </div>
    </div>
  );
}
