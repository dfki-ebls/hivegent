import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ToolFallback } from "@/components/chat/tools/ToolFallback";
import type { ToolPart } from "@/lib/chat/tool-part";

// The card lives inside a `Conversation`; only its scroll handler is needed here.
vi.mock("@/hooks/chat/use-stay-scrolled-on-toggle", () => ({
  useStayScrolledOnToggle: () => () => {},
}));

function part(state: ToolPart["state"]): ToolPart {
  return {
    type: "tool-write_document",
    toolCallId: "call-1",
    state,
    input: { file_path: "~/notes.md" },
    ...(state === "approval-requested" ? { approval: { id: "call-1" } } : {}),
  } as ToolPart;
}

describe("ToolFallback", () => {
  it("expands when the running call turns into an approval request", () => {
    const noop = vi.fn<(id: string) => void>();
    const props = { toolName: "write_document", onApprove: noop, onDeny: noop };
    const { rerender } = render(<ToolFallback part={part("input-available")} {...props} />);

    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();

    rerender(<ToolFallback part={part("approval-requested")} {...props} />);

    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Deny" })).toBeTruthy();
  });
});
