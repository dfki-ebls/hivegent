import type { ReactNode } from "react";
import type { SyncOutput, ToolPart } from "@/lib/chat/tool-part";
import { CreatePlanTool } from "@/components/chat/tools/create-plan";
import { syncGrepOutput } from "@/components/chat/tools/grep";
import { syncReadDocumentOutput } from "@/components/chat/tools/read-document";
import { syncSearchOutput } from "@/components/chat/tools/search";
import { syncWebFetchOutput } from "@/components/chat/tools/web-fetch";
import { syncWebSearchOutput } from "@/components/chat/tools/web-search";

export interface ToolRenderProps {
  part: ToolPart;
  onExecutePlan?: () => void;
}

export interface ToolHandler {
  render?: (props: ToolRenderProps) => ReactNode;
  syncOutput?: SyncOutput;
}

const TOOL_HANDLERS: Record<string, ToolHandler> = {
  search: { syncOutput: syncSearchOutput },
  read_document: { syncOutput: syncReadDocumentOutput },
  grep: { syncOutput: syncGrepOutput },
  web_search: { syncOutput: syncWebSearchOutput },
  web_fetch: { syncOutput: syncWebFetchOutput },
  create_plan: { render: ({ part, onExecutePlan }) => CreatePlanTool({ part, onExecutePlan }) },
};

export function getToolHandler(name: string): ToolHandler | undefined {
  return TOOL_HANDLERS[name];
}
