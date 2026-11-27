import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { MessageLiveProvider } from "@/components/chat/chat-context";
import { getToolHandler } from "@/components/chat/tools/registry";
import { ToolFallback } from "@/components/chat/tools/tool-fallback";
import type { ToolPart } from "@/lib/chat/tool-part";

const part = (state: ToolPart["state"], extra: Partial<ToolPart> = {}) =>
  ({
    type: "tool-write_document",
    toolCallId: "call-1",
    state,
    input: { file_path: "~/notes.md" },
    ...extra,
  }) as ToolPart;

const fallback = (toolPart: ToolPart, prompt = false) => (
  <ToolFallback toolName="write_document" part={toolPart} prompt={prompt} />
);

it("hides the internal changeset id of apply_changes", () => {
  const call: ToolPart = {
    type: "tool-apply_changes",
    toolCallId: "call-1",
    state: "output-available",
    input: { changeset_id: "internal-id" },
    output: "Applied changes.",
  };
  render(getToolHandler("apply_changes")?.render?.({ part: call, metadata: null, prompt: false }));
  fireEvent.click(screen.getByText("Apply Changes"));

  expect(screen.getByText("Applied changes.")).toBeTruthy();
  expect(screen.queryByText(/changeset_id/)).toBeNull();
});

it("expands when the running call turns into an approval request", () => {
  const { rerender } = render(
    <MessageLiveProvider value="streaming">
      {fallback(part("input-available"))}
    </MessageLiveProvider>,
  );

  expect(screen.queryByRole("group", { name: /Write Document/ })).toBeNull();

  rerender(
    <MessageLiveProvider value="waiting">
      {fallback(part("approval-requested", { approval: { id: "call-1" } }), true)}
    </MessageLiveProvider>,
  );

  expect(screen.getByRole("group", { name: /Write Document/ })).toBeTruthy();
});

it("shows the note the user sent with the decision, and a list output item by item", () => {
  const denied = part("output-denied", {
    approval: { id: "call-1", approved: false, reason: "Wrong folder." },
  });
  const { unmount } = render(fallback(denied));
  fireEvent.click(screen.getByText("Write Document"));

  expect(screen.getByText("Wrong folder.", { exact: false }).textContent).toBe(
    "Denied · Your instruction: Wrong folder.",
  );
  unmount();

  const note = "Note from the user who approved this call: Keep it short.";
  render(fallback(part("output-available", { output: ["Wrote '~/notes.md'.", note] })));
  fireEvent.click(screen.getByText("Write Document"));

  expect(screen.getByText("Wrote '~/notes.md'.").closest("pre")).not.toBe(
    screen.getByText(note).closest("pre"),
  );
});
