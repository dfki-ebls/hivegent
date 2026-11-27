import type { UIMessage } from "ai";
import { expect, it } from "vitest";

import { i18n } from "@/i18n";
import {
  getToolPartInfo,
  indexToolData,
  type ToolPart,
  toolDisplayName,
  toolLineState,
} from "@/lib/chat/tool-part";

type Part = UIMessage["parts"][number];

const read = (toolCallId: string): Part => ({
  type: "tool-read_document",
  toolCallId,
  state: "output-available",
  input: { reads: [{ file_path: `${toolCallId}.md` }] },
  output: toolCallId,
});

const data = (id: string, content: string): Part => ({
  type: "data-tool-output",
  id,
  data: { start_line: 1, end_line: 1, total_lines: 1, content },
});

it("matches the data part of parallel calls by id, not adjacency, and waits for it", () => {
  const parts = [read("a"), read("b"), data("b", "B"), data("a", "A")];
  const index = indexToolData(parts);
  const metadata = (part: Part | undefined) => part && getToolPartInfo(part, index)?.metadata;

  expect(metadata(parts[0])).toMatchObject({ content: "A" });
  expect(metadata(parts[1])).toMatchObject({ content: "B" });
  expect(getToolPartInfo(read("c"), index)?.metadata).toBeNull();
});

it("names built-in tools from the catalog and title-cases unknown ones", () => {
  expect(toolDisplayName(i18n.t, "list_documents")).toBe("List Documents");
  expect(toolDisplayName(i18n.t, "mcp_list_issues")).toBe("Mcp List Issues");
});

it("runs a call only while its message streams, awaits an approval until a later message denies it", () => {
  const call = { type: "dynamic-tool", toolName: "search", toolCallId: "1", input: {} } as const;
  const running: ToolPart = { ...call, state: "input-available" };
  const asking: ToolPart = { ...call, state: "approval-requested", approval: { id: "1" } };
  const interrupted: ToolPart = {
    ...call,
    state: "output-available",
    output: "ended early",
    callProviderMetadata: { pydantic_ai: { outcome: "interrupted" } },
  };

  expect(toolLineState(running, "streaming")).toEqual({ label: "running" });
  expect(toolLineState(running, "waiting")).toEqual({ failed: true, label: "stopped" });
  expect(toolLineState(asking, "waiting")).toEqual({ label: "awaitingApproval" });
  expect(toolLineState(asking, "stale")).toEqual({ label: "denied" });
  expect(toolLineState(interrupted, "stale")).toEqual({ failed: true, label: "stopped" });
});
