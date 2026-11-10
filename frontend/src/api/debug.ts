import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";

import { jsonRequest, requestJson } from "@/api/client";
import { apiUrl } from "@/lib/health";
import {
  type ChatRequestConfig,
  type ConversationSummary,
  ConversationSummarySchema,
} from "@/api/conversations";
import type { ChatMessage } from "@/lib/chat/message";

/** A chat debugger report, free of the analyzed conversation's data. */
const DebugReportSchema = z.object({
  markdown: z.string(),
  /** Spans masked as `<REDACTED_n>` after the model failed to replace them. */
  masked: z.number(),
});
export type DebugReport = z.infer<typeof DebugReportSchema>;

/** The run configuration a chat debugger request carries beside its messages. */
export type DebugRequestConfig = Pick<ChatRequestConfig, "llm" | "reasoning_effort">;

/** The route a chat debugger session about *conversationId* is sent to. */
export function debugChatUrl(conversationId: string): string {
  return apiUrl(`/debug/conversations/${encodeURIComponent(conversationId)}/chat`);
}

/** Fetch any user's conversation summary for the chat debugger (admin only). */
function getDebugConversation(id: string): Promise<ConversationSummary> {
  return requestJson(
    apiUrl(`/debug/conversations/${encodeURIComponent(id)}`),
    "fetchDebugConversation",
    ConversationSummarySchema,
  );
}

/** Condense a chat debugger session into a report free of the conversation's data (admin only). */
export function writeDebugReport(
  id: string,
  messages: ChatMessage[],
  config: DebugRequestConfig,
): Promise<DebugReport> {
  return requestJson(
    apiUrl(`/debug/conversations/${encodeURIComponent(id)}/report`),
    "writeDebugReport",
    DebugReportSchema,
    jsonRequest("POST", { ...config, messages }),
  );
}

const all = ["debug"] as const;

export const debugQueries = {
  /** Any user's conversation summary, confirming the ID the chat debugger opens. */
  conversation: (id: string) =>
    queryOptions({
      queryKey: [...all, "conversation", id],
      queryFn: () => getDebugConversation(id),
      retry: false,
    }),
};
