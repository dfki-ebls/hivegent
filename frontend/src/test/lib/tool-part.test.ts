import type { UIMessage } from "ai";
import { describe, expect, it } from "vitest";

import { i18n } from "@/i18n";
import {
  getToolPartInfo,
  indexToolData,
  type ToolPart,
  toolDisplayName,
  toolLineState,
} from "@/lib/chat/tool-part";

/**
 * The AI SDK appends ``data-tool-output`` parts to the end of
 * ``message.parts`` in arrival (completion) order, so for parallel tool
 * calls a tool's data part is never adjacent to it.  ``getToolPartInfo``
 * must correlate by tool-call id rather than position.
 */
describe("getToolPartInfo", () => {
  type Part = UIMessage["parts"][number];

  const toolPart = (toolCallId: string, filePath: string): Part => ({
    type: "tool-read_document",
    toolCallId,
    state: "output-available",
    input: { reads: [{ file_path: filePath }] },
    output: filePath,
  });

  const dataPart = (id: string, content: string): Part => ({
    type: "data-tool-output",
    id,
    data: { start_line: 1, end_line: 1, total_lines: 1, content },
  });

  const contentOf = (parts: UIMessage["parts"], index: number): string | undefined => {
    const part = parts[index];
    const info = part && getToolPartInfo(part, indexToolData(parts));
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
    ];

    expect(contentOf(parts, 0)).toBe("A");
    expect(contentOf(parts, 1)).toBe("B");
    expect(contentOf(parts, 2)).toBe("C");
  });

  it("returns null metadata when the data part has not streamed yet", () => {
    const part = toolPart("call-a", "a.md");
    expect(getToolPartInfo(part, indexToolData([part]))?.metadata).toBeNull();
  });
});

describe("toolDisplayName", () => {
  it("names built-in tools from the catalog and title-cases unknown ones", () => {
    expect(toolDisplayName(i18n.t, "list_documents")).toBe("List Documents");
    expect(toolDisplayName(i18n.t, "mcp_list_issues")).toBe("Mcp List Issues");
  });
});

describe("toolLineState", () => {
  const call = { type: "dynamic-tool", toolName: "search", toolCallId: "1", input: {} } as const;
  const running: ToolPart = { ...call, state: "input-available" };
  const asking: ToolPart = { ...call, state: "approval-requested", approval: { id: "1" } };

  it("runs a call only while its message streams and stops it otherwise", () => {
    expect(toolLineState(running, "streaming")).toEqual({ label: "running" });
    expect(toolLineState(running, "waiting")).toEqual({ failed: true, label: "stopped" });
  });

  it("awaits an approval until a later message abandons it", () => {
    expect(toolLineState(asking, "waiting")).toEqual({ label: "awaitingApproval" });
    expect(toolLineState(asking, "stale")).toEqual({ failed: true, label: "stopped" });
  });
});
