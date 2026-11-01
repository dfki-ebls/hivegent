import type { UIMessage } from "ai";
import { useEffect, useRef } from "react";
import { getToolHandler } from "@/components/chat/tools/registry";
import { getToolPartInfo, indexToolData } from "@/lib/chat/tool-part";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";

/**
 * Sync every finished tool output of a transcript into the fetched-documents store, each payload once.
 * The store starts empty for every transcript and once a rewrite drops a message,
 * such as an edit, a retry or a regenerate, so it holds what the transcript read.
 *
 * The SDK snapshots a streaming message shallowly, so a payload keeps its identity
 * across updates and one already synced is skipped rather than parsed again on every tick.
 */
export function useToolOutputSync(messages: UIMessage[]) {
  const previous = useRef<UIMessage[] | null>(null);

  useEffect(() => {
    const { addChunk, markFullDocument, addImage, claimOutput, clearAll } =
      useFetchedDocumentsStore.getState();
    const ids = new Set(messages.map((message) => message.id));

    if (!previous.current || previous.current.some((message) => !ids.has(message.id))) {
      clearAll();
    }

    previous.current = messages;

    for (const message of messages) {
      const toolData = indexToolData(message.parts);

      for (const part of message.parts) {
        if (!("toolCallId" in part) || part.state !== "output-available") continue;

        // Every handler reads the structured payload, so a part without one has nothing
        // to sync, and claiming it first spares a synced part even its text's parse.
        const payload = toolData.get(part.toolCallId);

        if (typeof payload !== "object" || payload === null || !claimOutput(payload)) continue;

        const info = getToolPartInfo(part, toolData);

        if (!info) continue;

        getToolHandler(info.toolName)?.syncOutput?.({
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
  }, [messages]);
}
