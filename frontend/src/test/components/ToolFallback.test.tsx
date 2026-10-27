import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ToolFallback } from "@/components/chat/tools/ToolFallback";
import { getToolHandler } from "@/components/chat/tools/registry";
import type { ToolPart } from "@/lib/chat/tool-part";

function part(state: ToolPart["state"], approval?: ToolPart["approval"]): ToolPart {
  return {
    type: "tool-write_document",
    toolCallId: "call-1",
    state,
    input: { file_path: "~/notes.md" },
    approval,
  } as ToolPart;
}

describe("ToolFallback", () => {
  it("hides the internal changeset id of apply_changes", () => {
    const call: ToolPart = {
      type: "tool-apply_changes",
      toolCallId: "call-1",
      state: "output-available",
      input: { changeset_id: "internal-id" },
      output: "Applied changes.",
    };
    render(getToolHandler("apply_changes")?.render?.({ part: call, metadata: null }));
    fireEvent.click(screen.getByText("Apply Changes"));

    expect(screen.getByText("Applied changes.")).toBeTruthy();
    expect(screen.queryByText(/changeset_id/)).toBeNull();
  });

  it("expands when the running call turns into an approval request", () => {
    const { rerender } = render(
      <ToolFallback toolName="write_document" part={part("input-available")} />,
    );

    expect(screen.queryByRole("group", { name: "Write Document" })).toBeNull();

    rerender(
      <ToolFallback
        toolName="write_document"
        part={part("approval-requested", { id: "call-1" })}
      />,
    );

    expect(screen.getByRole("group", { name: "Write Document" })).toBeTruthy();
  });

  it("shows the note the user sent with the decision", () => {
    render(
      <ToolFallback
        toolName="write_document"
        part={part("output-denied", { id: "call-1", approved: false, reason: "Wrong folder." })}
      />,
    );
    fireEvent.click(screen.getByText("Write Document"));

    expect(screen.getByText("Wrong folder.", { exact: false }).textContent).toBe(
      "Denied · Your instruction: Wrong folder.",
    );
  });

  it("shows a list output item by item, such as a result and its approval note", () => {
    const note = "Note from the user who approved this call: Keep it short.";
    render(
      <ToolFallback
        toolName="write_document"
        part={{ ...part("output-available"), output: ["Wrote '~/notes.md'.", note] } as ToolPart}
      />,
    );
    fireEvent.click(screen.getByText("Write Document"));

    const result = screen.getByText("Wrote '~/notes.md'.").closest("pre");

    expect(result).toBeTruthy();
    expect(screen.getByText(note).closest("pre")).not.toBe(result);
  });
});
