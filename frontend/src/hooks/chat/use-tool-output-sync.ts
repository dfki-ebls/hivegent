import type { UIMessage } from "@ai-sdk/react";
import { useEffect } from "react";
import { getToolHandler } from "@/components/chat/tools/registry";
import { getToolPartInfo, indexToolData } from "@/lib/chat/tool-part";
import type {
  AddChunk,
  AddImage,
  MarkFullDocument,
} from "@/stores/fetched-documents-store";

export function useToolOutputSync(
  messages: UIMessage[],
  addChunk: AddChunk,
  markFullDocument: MarkFullDocument,
  addImage: AddImage,
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
        handler?.syncOutput?.({
          input: info.input,
          text: info.text,
          metadata: info.metadata,
          sourceId: info.toolCallId,
          addChunk,
          markFullDocument,
          addImage,
        });
      }
    }
  }, [messages, addChunk, markFullDocument, addImage]);
}
