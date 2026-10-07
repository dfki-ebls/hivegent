import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ToolFallback } from "@/components/chat/tools/ToolFallback";
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
  it("expands when the running call turns into an approval request", () => {
    const { rerender } = render(
      <ToolFallback toolName="write_document" part={part("input-available")} />,
    );

    expect(screen.queryByRole("alert")).toBeNull();

    rerender(
      <ToolFallback
        toolName="write_document"
        part={part("approval-requested", { id: "call-1" })}
      />,
    );

    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("shows the note the user sent with the decision", () => {
    render(
      <ToolFallback
        toolName="write_document"
        part={part("output-denied", { id: "call-1", approved: false, reason: "Wrong folder." })}
      />,
    );
    fireEvent.click(screen.getByText("Write Document"));

    expect(screen.getByRole("alert").textContent).toBe("DeniedYour note: Wrong folder.");
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

    expect(screen.getByText("Wrote '~/notes.md'.").tagName).toBe("PRE");
    expect(screen.getByText(note).tagName).toBe("PRE");
  });
});
