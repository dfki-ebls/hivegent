import type { ReactNode } from "react";
import type { SyncOutput, ToolPart } from "@/lib/chat/tool-part";
import { syncGrepOutput } from "@/components/chat/tools/grep";
import {
  ReadBinaryDocumentTool,
  syncReadBinaryDocumentOutput,
} from "@/components/chat/tools/read-binary-document";
import { syncReadDocumentOutput } from "@/components/chat/tools/read-document";
import { RunPythonTool } from "@/components/chat/tools/run-python";
import { syncSearchOutput } from "@/components/chat/tools/search";
import { syncWebFetchOutput } from "@/components/chat/tools/web-fetch";
import { syncWebSearchOutput } from "@/components/chat/tools/web-search";

export interface ToolRenderProps {
  part: ToolPart;
  metadata: unknown;
}

export interface ToolHandler {
  render?: (props: ToolRenderProps) => ReactNode;
  syncOutput?: SyncOutput;
}

const TOOL_HANDLERS: Record<string, ToolHandler> = {
  search: { syncOutput: syncSearchOutput },
  read_document: { syncOutput: syncReadDocumentOutput },
  read_binary_document: {
    render: ({ part, metadata }) => <ReadBinaryDocumentTool part={part} metadata={metadata} />,
    syncOutput: syncReadBinaryDocumentOutput,
  },
  grep: { syncOutput: syncGrepOutput },
  web_search: { syncOutput: syncWebSearchOutput },
  web_fetch: { syncOutput: syncWebFetchOutput },
  run_python: {
    render: ({ part, metadata }) => <RunPythonTool part={part} metadata={metadata} />,
  },
};

export function getToolHandler(name: string): ToolHandler | undefined {
  return TOOL_HANDLERS[name];
}
