import type { ReactNode } from "react";
import type { SyncOutput, ToolPart } from "@/lib/chat/tool-part";
import { CreatePlanTool } from "@/components/chat/tools/create-plan";
import { syncGrepOutput } from "@/components/chat/tools/grep";
import { ReadBinaryDocumentTool } from "@/components/chat/tools/read-binary-document";
import { syncReadDocumentOutput } from "@/components/chat/tools/read-document";
import { syncSearchOutput } from "@/components/chat/tools/search";
import { syncWebFetchOutput } from "@/components/chat/tools/web-fetch";
import { syncWebSearchOutput } from "@/components/chat/tools/web-search";

export interface ToolRenderProps {
  part: ToolPart;
  metadata: unknown;
  onExecutePlan?: () => void;
}

export interface ToolHandler {
  render?: (props: ToolRenderProps) => ReactNode;
  syncOutput?: SyncOutput;
}

const TOOL_HANDLERS: Record<string, ToolHandler> = {
  search: { syncOutput: syncSearchOutput },
  read_document: { syncOutput: syncReadDocumentOutput },
  read_binary_document: {
    render: ({ part, metadata }) => (
      <ReadBinaryDocumentTool part={part} metadata={metadata} />
    ),
  },
  grep: { syncOutput: syncGrepOutput },
  web_search: { syncOutput: syncWebSearchOutput },
  web_fetch: { syncOutput: syncWebFetchOutput },
  create_plan: {
    render: ({ part, onExecutePlan }) => (
      <CreatePlanTool part={part} onExecutePlan={onExecutePlan} />
    ),
  },
};

export function getToolHandler(name: string): ToolHandler | undefined {
  return TOOL_HANDLERS[name];
}
