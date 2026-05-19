import type { UIMessage } from "@ai-sdk/react";
import { useEffect } from "react";
import { getToolHandler } from "@/components/chat/tools/registry";
import { getToolPartInfo } from "@/lib/chat/tool-part";
import type { FetchedChunk } from "@/lib/types";

export function useToolOutputSync(
  messages: UIMessage[],
  addChunk: (chunk: Omit<FetchedChunk, "id">) => void,
  markFullDocument: (filename: string, content: string, source: string) => void,
) {
  useEffect(() => {
    for (const message of messages) {
      const parts = message.parts;
      if (!parts) continue;
      for (let i = 0; i < parts.length; i++) {
        const info = getToolPartInfo(parts, i);
        if (!info || info.state !== "output-available") continue;
        const handler = getToolHandler(info.toolName);
        handler?.syncOutput?.(info.input, info.text, info.metadata, addChunk, markFullDocument);
      }
    }
  }, [messages, addChunk, markFullDocument]);
}
