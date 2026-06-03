import type { UIMessage } from "@ai-sdk/react";
import { describe, expect, it } from "vitest";

import { getToolPartInfo, indexToolData } from "@/lib/chat/tool-part";

/**
 * The AI SDK appends ``data-tool-output`` parts to the end of
 * ``message.parts`` in arrival (completion) order, so for parallel tool
 * calls a tool's data part is never adjacent to it.  ``getToolPartInfo``
 * must correlate by tool-call id rather than position.
 */
describe("getToolPartInfo", () => {
  const toolPart = (toolCallId: string, filePath: string) => ({
    type: "tool-read_document",
    toolCallId,
    state: "output-available",
    input: { file_path: filePath },
    output: filePath,
  });

  const dataPart = (id: string, content: string) => ({
    type: "data-tool-output",
    id,
    data: { start_line: 1, end_line: 1, total_lines: 1, content },
  });

  const contentOf = (parts: UIMessage["parts"], index: number): string | undefined => {
    const info = getToolPartInfo(parts[index], indexToolData(parts));
    return (info?.metadata as { content: string } | undefined)?.content;
  };

  it("matches the data part for parallel calls by id, not adjacency", () => {
    // Three tool parts clustered first, then their data parts at the end
    // in a *different* order — exactly the parallel streaming layout.
    const parts = [
      toolPart("call-a", "a.md"),
      toolPart("call-b", "b.md"),
      toolPart("call-c", "c.md"),
      dataPart("call-c", "C"),
      dataPart("call-a", "A"),
      dataPart("call-b", "B"),
    ] as unknown as UIMessage["parts"];

    expect(contentOf(parts, 0)).toBe("A");
    expect(contentOf(parts, 1)).toBe("B");
    expect(contentOf(parts, 2)).toBe("C");
  });

  it("returns null metadata when the data part has not streamed yet", () => {
    const parts = [toolPart("call-a", "a.md")] as unknown as UIMessage["parts"];
    expect(getToolPartInfo(parts[0], indexToolData(parts))?.metadata).toBeNull();
  });
});
