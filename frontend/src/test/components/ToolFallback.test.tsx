import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ToolFallback } from "@/components/chat/tools/ToolFallback";
import { ToolApprovalProvider } from "@/hooks/chat/use-tool-approval";
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

function renderFallback(state: ToolPart["state"], blockedReason?: string) {
  const decide = vi.fn<(id: string, approved: boolean) => void>();
  const view = render(
    <ToolApprovalProvider value={{ decide, blockedReason }}>
      <ToolFallback toolName="write_document" part={part(state)} />
    </ToolApprovalProvider>,
  );
  return { ...view, decide };
}

describe("ToolFallback", () => {
  it("expands when the running call turns into an approval request", () => {
    const { rerender, decide } = renderFallback("input-available");

    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();

    rerender(
      <ToolApprovalProvider value={{ decide }}>
        <ToolFallback toolName="write_document" part={part("approval-requested")} />
      </ToolApprovalProvider>,
    );

    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Deny" })).toBeTruthy();
  });

  // A decision made while the previous turn is still streaming is recorded but
  // never dispatched by the SDK, so the buttons stay blocked until it settles.
  it("blocks the decision, with its reason, when the gate is closed", () => {
    renderFallback("approval-requested", "Not right now.");

    expect(screen.getByRole("button", { name: "Approve" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Deny" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("Not right now.")).toBeTruthy();
  });
});
