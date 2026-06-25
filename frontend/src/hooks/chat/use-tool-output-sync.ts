import type { UIMessage } from "@ai-sdk/react";
import { useEffect } from "react";
import { getToolHandler } from "@/components/chat/tools/registry";
import { getToolPartInfo, indexToolData } from "@/lib/chat/tool-part";
import type { ChunkTool, FetchedChunk, FetchedImage } from "@/lib/types";

export function useToolOutputSync(
  messages: UIMessage[],
  addChunk: (chunk: Omit<FetchedChunk, "id">) => void,
  markFullDocument: (filename: string, content: string, tool: ChunkTool) => void,
  addImage: (filename: string, image: FetchedImage) => void,
) {
  useEffect(() => {
    for (const message of messages) {
      const parts = message.parts;
      if (!parts) continue;
      const toolData = indexToolData(parts);
      for (const part of parts) {
        const info = getToolPartInfo(part, toolData);
        if (!info || info.state !== "output-available") continue;
        const handler = getToolHandler(info.toolName);
        handler?.syncOutput?.(
          info.input,
          info.text,
          info.metadata,
          addChunk,
          markFullDocument,
          addImage,
        );
      }
    }
  }, [messages, addChunk, markFullDocument, addImage]);
}
