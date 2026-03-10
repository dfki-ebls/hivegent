import type { UIMessage } from "@ai-sdk/react";
import type {
  ChunkPosition,
  DocumentRange,
  FetchedChunk,
  GrepMatch,
  RetrievedChunk,
} from "@/lib/types";
import type { DynamicToolUIPart, ToolUIPart } from "ai";

import {
  Confirmation,
  ConfirmationAccepted,
  ConfirmationAction,
  ConfirmationActions,
  ConfirmationRejected,
  ConfirmationRequest,
} from "@/components/ai-elements/confirmation";
import { CodeBlock } from "@/components/ai-elements/code-block";
import { Tool, ToolContent, ToolHeader } from "@/components/ai-elements/tool";
import {
  ToolError,
  ToolKeyValue,
  ToolParameters,
  ToolResult,
  ToolSection,
} from "@/components/ToolDisplay";

export type ToolPart = ToolUIPart | DynamicToolUIPart;

interface ToolPartInfo {
  toolName: string;
  state: ToolPart["state"];
  input: Record<string, unknown> | undefined;
  output: unknown;
}

interface ToolPartDisplayProps {
  toolName: string;
  part: ToolPart;
  onApprove?: (id: string) => void;
  onDeny?: (id: string) => void;
}

function getToolName(part: { type: string; toolName?: string }): string | null {
  if (part.type === "dynamic-tool" && part.toolName) return part.toolName;
  if (part.type.startsWith("tool-")) return part.type.replace("tool-", "");
  return null;
}

function parseJson<T>(value: unknown): T | undefined {
  if (typeof value === "string") {
    try {
      return JSON.parse(value) as T;
    } catch {
      return undefined;
    }
  }
  return value as T;
}

function prettyPrint(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  if (typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }
  return String(value as number | boolean);
}

export function processToolOutput(
  toolName: string,
  input: Record<string, unknown> | undefined,
  output: unknown,
  addChunk: (chunk: Omit<FetchedChunk, "id">) => void,
  markFullDocument: (filename: string, content: string, source: string) => void,
) {
  if (!input || output == null) return;

  switch (toolName) {
    case "semantic_search": {
      const chunks = output as RetrievedChunk[];
      if (!chunks?.length) return;

      const query = input.query as string;
      const source = `search${query ? `: ${query}` : ""}`;
      for (const chunk of chunks) {
        const position: ChunkPosition = {
          type: "chunk_index",
          chunkIndex: chunk.chunk_index,
        };
        addChunk({
          filename: chunk.filename,
          content: chunk.text,
          source,
          score: chunk.score,
          position,
        });
      }
      return;
    }
    case "get_document": {
      const filename = input.filename as string;
      const content = typeof output === "string" ? output : null;
      if (filename && content) {
        markFullDocument(filename, content, "get_document");
      }
      return;
    }
    case "get_document_lines": {
      const filename = input.filename as string;
      const result = output as DocumentRange;
      if (filename && result?.content) {
        const position: ChunkPosition = {
          type: "line_range",
          startLine: result.start_line,
          endLine: result.end_line,
        };
        addChunk({
          filename,
          content: result.content,
          source: `lines ${result.start_line}-${result.end_line}`,
          position,
        });
      }
      return;
    }
    case "grep": {
      const matches = output as GrepMatch[];
      const pattern = input.pattern as string;
      if (!matches?.length || !pattern) return;

      const source = `grep: ${pattern}`;
      for (const match of matches) {
        if (match.line <= 0) continue;

        const position: ChunkPosition = {
          type: "line",
          line: match.line,
        };
        addChunk({
          filename: match.filename,
          content: match.content ?? "",
          source,
          position,
        });
      }
      return;
    }
    case "get_chunk": {
      const filename = input.filename as string;
      const chunkIndex = input.chunk_index as number;
      if (filename && typeof output === "string") {
        const position: ChunkPosition = {
          type: "chunk_index",
          chunkIndex,
        };
        addChunk({
          filename,
          content: output,
          source: `chunk ${chunkIndex}`,
          position,
        });
      }
      return;
    }
    case "web_search": {
      const results = output as { title: string; href: string; body: string }[];
      if (!results?.length) return;
      const query = input.query as string;
      const source = `web: ${query ?? "search"}`;
      for (const r of results) {
        if (!r.href) continue;
        addChunk({
          filename: r.href,
          content: r.body || r.title,
          source,
          position: { type: "web_result", url: r.href },
        });
      }
      return;
    }
    case "web_fetch": {
      const url = input.url as string;
      const content = typeof output === "string" ? output : null;
      if (url && content && !content.startsWith("Error:")) {
        markFullDocument(url, content, "web_fetch");
      }
      return;
    }
  }
}

export function getToolPartInfo(part: UIMessage["parts"][number]): ToolPartInfo | null {
  const typed = part as {
    type: string;
    toolName?: string;
    state?: ToolPart["state"];
    input?: unknown;
    output?: unknown;
  };
  const toolName = getToolName(typed);
  if (!toolName) return null;

  return {
    toolName,
    state: typed.state ?? "output-available",
    input: parseJson<Record<string, unknown>>(typed.input),
    output: parseJson<unknown>(typed.output) ?? typed.output,
  };
}

function SearchToolDisplay({ toolName, part }: ToolPartDisplayProps) {
  const state: ToolPart["state"] = part.state ?? "output-available";
  const input = parseJson<{ query: string; type?: string; top_k?: number }>(part.input);
  const output = parseJson<RetrievedChunk[]>(part.output);
  const title = input?.type === "sparse" ? "Keyword Search" : "Semantic Search";

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
            {output.map((chunk) => (
              <ToolKeyValue
                key={`${chunk.filename}::${chunk.chunk_index}`}
                label={`${chunk.filename} #${chunk.chunk_index}`}
                value={`${(chunk.score * 100).toFixed(0)}% match`}
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

function EditDocumentToolDisplay({ part, onApprove, onDeny }: ToolPartDisplayProps) {
  const state: ToolPart["state"] = part.state ?? "output-available";
  const approval = part.approval as ToolUIPart["approval"];
  const input = parseJson<{
    filename: string;
    old_string: string;
    new_string: string;
  }>(part.input);

  return (
    <Tool defaultOpen={state === "approval-requested"}>
      <ToolHeader title="Edit Document" type="tool-edit_document" state={state} />
      <ToolContent>
        {input && (
          <ToolSection title="Parameters">
            <ToolKeyValue label="File" value={input.filename} />
            <ToolKeyValue
              label="Replace"
              value={<pre className="whitespace-pre-wrap text-xs">{input.old_string}</pre>}
            />
            <ToolKeyValue
              label="With"
              value={<pre className="whitespace-pre-wrap text-xs">{input.new_string}</pre>}
            />
          </ToolSection>
        )}
        <Confirmation approval={approval} state={state}>
          <ConfirmationRequest>
            <span className="text-sm">
              Allow the assistant to edit <strong>{input?.filename}</strong>?
            </span>
          </ConfirmationRequest>
          <ConfirmationAccepted>
            <span className="text-sm text-green-700 dark:text-green-400">Edit approved</span>
          </ConfirmationAccepted>
          <ConfirmationRejected>
            <span className="text-sm text-orange-700 dark:text-orange-400">Edit denied</span>
          </ConfirmationRejected>
          <ConfirmationActions>
            <ConfirmationAction variant="outline" onClick={() => onDeny?.(approval?.id ?? "")}>
              Deny
            </ConfirmationAction>
            <ConfirmationAction onClick={() => onApprove?.(approval?.id ?? "")}>
              Approve
            </ConfirmationAction>
          </ConfirmationActions>
        </Confirmation>
        {part.output !== undefined && (
          <ToolResult>
            <pre className="whitespace-pre-wrap text-xs font-mono">{prettyPrint(part.output)}</pre>
          </ToolResult>
        )}
        {part.errorText && <ToolError message={part.errorText} />}
      </ToolContent>
    </Tool>
  );
}

function WriteDocumentToolDisplay({ part, onApprove, onDeny }: ToolPartDisplayProps) {
  const state: ToolPart["state"] = part.state ?? "output-available";
  const approval = part.approval as ToolUIPart["approval"];
  const input = parseJson<{ filename: string; content: string; mode?: string }>(part.input);
  const modeLabel = input?.mode ?? "replace";

  return (
    <Tool defaultOpen={state === "approval-requested"}>
      <ToolHeader title="Write Document" type="tool-write_document" state={state} />
      <ToolContent>
        {input && (
          <ToolSection title="Parameters">
            <ToolKeyValue label="File" value={input.filename} />
            <ToolKeyValue label="Mode" value={modeLabel} />
            <ToolKeyValue
              label="Content"
              value={
                <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap text-xs">
                  {input.content}
                </pre>
              }
            />
          </ToolSection>
        )}
        <Confirmation approval={approval} state={state}>
          <ConfirmationRequest>
            <span className="text-sm">
              Allow the assistant to <strong>{modeLabel}</strong> <strong>{input?.filename}</strong>
              ?
            </span>
          </ConfirmationRequest>
          <ConfirmationAccepted>
            <span className="text-sm text-green-700 dark:text-green-400">Write approved</span>
          </ConfirmationAccepted>
          <ConfirmationRejected>
            <span className="text-sm text-orange-700 dark:text-orange-400">Write denied</span>
          </ConfirmationRejected>
          <ConfirmationActions>
            <ConfirmationAction variant="outline" onClick={() => onDeny?.(approval?.id ?? "")}>
              Deny
            </ConfirmationAction>
            <ConfirmationAction onClick={() => onApprove?.(approval?.id ?? "")}>
              Approve
            </ConfirmationAction>
          </ConfirmationActions>
        </Confirmation>
        {part.output !== undefined && (
          <ToolResult>
            <pre className="whitespace-pre-wrap text-xs font-mono">{prettyPrint(part.output)}</pre>
          </ToolResult>
        )}
        {part.errorText && <ToolError message={part.errorText} />}
      </ToolContent>
    </Tool>
  );
}

function GenericToolDisplay({ toolName, part }: ToolPartDisplayProps) {
  const state: ToolPart["state"] = part.state ?? "output-available";
  const input = parseJson<Record<string, unknown>>(part.input);

  return (
    <Tool defaultOpen={false}>
      <ToolHeader type={`tool-${toolName}`} state={state} />
      <ToolContent>
        {input && <ToolParameters params={input} />}
        {part.output !== undefined && (
          <ToolResult>
            <CodeBlock code={prettyPrint(part.output)} language="json" />
          </ToolResult>
        )}
        {state === "output-error" && part.errorText && <ToolError message={part.errorText} />}
      </ToolContent>
    </Tool>
  );
}

export function ToolPartDisplay({ toolName, part, onApprove, onDeny }: ToolPartDisplayProps) {
  if (toolName === "semantic_search") {
    return <SearchToolDisplay toolName={toolName} part={part} />;
  }
  if (toolName === "edit_document") {
    return (
      <EditDocumentToolDisplay
        toolName={toolName}
        part={part}
        onApprove={onApprove}
        onDeny={onDeny}
      />
    );
  }
  if (toolName === "write_document") {
    return (
      <WriteDocumentToolDisplay
        toolName={toolName}
        part={part}
        onApprove={onApprove}
        onDeny={onDeny}
      />
    );
  }
  return <GenericToolDisplay toolName={toolName} part={part} />;
}
