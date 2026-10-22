import type { UIMessage } from "@ai-sdk/react";
import { useEffect } from "react";
import { getToolHandler } from "@/components/chat/tools/registry";
import { getToolPartInfo, indexToolData } from "@/lib/chat/tool-part";
import type {
  AddChunk,
  AddImage,
  ClaimOutput,
  MarkFullDocument,
} from "@/stores/fetched-documents-store";

/**
 * Sync every finished tool output into the fetched-documents store, each payload once.
 *
 * The SDK snapshots a streaming message shallowly, so a payload keeps its identity
 * across updates and one already synced is skipped rather than parsed again on every tick.
 */
export function useToolOutputSync(
  messages: UIMessage[],
  addChunk: AddChunk,
  markFullDocument: MarkFullDocument,
  addImage: AddImage,
  claimOutput: ClaimOutput,
) {
  useEffect(() => {
    for (const message of messages) {
      const parts = message.parts;
      if (!parts) continue;
      const toolData = indexToolData(parts);
      for (const part of parts) {
        if (!("toolCallId" in part) || part.state !== "output-available") continue;

        // Every handler reads the structured payload, so a part without one has nothing
        // to sync, and claiming it first spares a synced part even its text's parse.
        const payload = toolData.get(part.toolCallId);
        if (typeof payload !== "object" || payload === null || !claimOutput(payload)) continue;

        const info = getToolPartInfo(part, toolData);
        if (!info) continue;

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
  }, [messages, addChunk, markFullDocument, addImage, claimOutput]);
}
