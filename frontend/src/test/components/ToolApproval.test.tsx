import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { ToolApprovalProvider, type ToolApprovalGate } from "@/hooks/chat/use-tool-approval";
import type { ChatMessage } from "@/lib/chat/chat-utils";

function request(id: string): ChatMessage["parts"][number] {
  return {
    type: "tool-send_mail",
    toolCallId: id,
    state: "approval-requested",
    input: { to: id },
    approval: { id },
  };
}

function approved(id: string): ChatMessage["parts"][number] {
  return {
    type: "tool-send_mail",
    toolCallId: id,
    state: "output-available",
    input: { to: id },
    output: "sent",
    approval: { id, approved: true },
  };
}

interface RenderOptions {
  blockedReason?: string;
  approvalMetadata?: Record<string, unknown>;
  part?: (id: string) => ChatMessage["parts"][number];
  /** Expand every tool card, since a resolved one renders collapsed. */
  open?: boolean;
}

function renderMessage(
  ids: string[],
  { blockedReason, approvalMetadata, part = request, open = false }: RenderOptions = {},
) {
  const decide = vi.fn<ToolApprovalGate["decide"]>();
  const noop = () => {};
  render(
    <ToolApprovalProvider value={{ decide, blockedReason }}>
      <MessageBubble
        message={{
          id: "m1",
          role: "assistant",
          parts: ids.map(part),
          metadata: { approvalMetadata },
        }}
        isLastMessage
        status="ready"
        editingId={null}
        onSetEditing={noop}
        onCancelEdit={noop}
        onSubmitEdit={noop}
        onRegenerate={noop}
      />
    </ToolApprovalProvider>,
  );

  if (open) {
    for (const trigger of screen.getAllByRole("button", { expanded: false })) {
      fireEvent.click(trigger);
    }
  }

  const cards = screen.getAllByRole("alert");

  return { decide, card: (index: number) => within(cards[index]) };
}

describe("tool approval", () => {
  it("sends a lone request from its card at once, with the note", () => {
    const { decide, card } = renderMessage(["call-1"]);

    expect(screen.queryByRole("button", { name: "Approve all" })).toBeNull();
    fireEvent.change(card(0).getByLabelText("Note for the assistant on Send Mail (optional)"), {
      target: { value: " Wrong folder. " },
    });
    fireEvent.click(card(0).getByRole("button", { name: "Deny" }));

    expect(decide.mock.calls).toEqual([
      [[{ id: "call-1", approved: false, reason: "Wrong folder." }]],
    ]);
  });

  it("stages the card choices of several requests and sends them on submit", () => {
    const { decide, card } = renderMessage(["call-1", "call-2"]);
    const submit = screen.getByRole("button", { name: "Submit decisions" });

    fireEvent.click(card(0).getByRole("button", { name: "Approve" }));
    fireEvent.click(card(1).getByRole("button", { name: "Approve" }));
    fireEvent.click(card(1).getByRole("button", { name: "Deny" }));
    fireEvent.change(card(1).getByLabelText("Note for the assistant on Send Mail (optional)"), {
      target: { value: "Not this one." },
    });

    expect(decide).not.toHaveBeenCalled();
    expect(card(1).getByRole("button", { name: "Deny", pressed: true })).toBeTruthy();
    expect(screen.getByText("2 of 2 decided")).toBeTruthy();
    fireEvent.click(submit);

    expect(decide.mock.calls).toEqual([
      [
        [
          { id: "call-1", approved: true },
          { id: "call-2", approved: false, reason: "Not this one." },
        ],
      ],
    ]);
  });

  it("answers several requests at once with the shared note", () => {
    const { decide } = renderMessage(["call-1", "call-2"]);

    fireEvent.change(screen.getByLabelText("Note for all actions (optional)"), {
      target: { value: "Keep backups." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Approve all" }));

    expect(decide.mock.calls).toEqual([
      [
        [
          { id: "call-1", approved: true, reason: "Keep backups." },
          { id: "call-2", approved: true, reason: "Keep backups." },
        ],
      ],
    ]);
  });

  it("asks with the changeset summary of any tool", () => {
    const summary = {
      creates: [{ path: "~/out.md", diff: "" }],
      updates: [],
      moves: [],
      deletes: [],
      mkdirs: [],
    };
    const { card } = renderMessage(["call-1"], { approvalMetadata: { "call-1": summary } });

    expect(
      card(0).getByText("Allow the assistant to apply this change to your workspace?"),
    ).toBeTruthy();
    expect(card(0).getByText("Created")).toBeTruthy();
    expect(card(0).getByText("~/out.md")).toBeTruthy();
  });

  it("keeps showing the changes once the call is resolved", () => {
    const summary = { creates: [], updates: [], moves: [], deletes: ["~/old.md"], mkdirs: [] };
    const { card } = renderMessage(["call-1"], {
      approvalMetadata: { "call-1": summary },
      part: approved,
      open: true,
    });

    expect(card(0).getByText("Approved")).toBeTruthy();
    expect(card(0).getByText("~/old.md")).toBeTruthy();
    expect(card(0).queryByRole("button", { name: "Approve" })).toBeNull();
  });

  // A decision made while the last chunks drain is never dispatched by the SDK.
  it.each([
    [["call-1"], ["Approve", "Deny"]],
    [["call-1", "call-2"], ["Approve all", "Deny all", "Submit decisions"]],
  ])("blocks sending %j while streaming, with the reason", (ids, buttons) => {
    renderMessage(ids, { blockedReason: "Not right now." });

    expect(screen.getByText("Not right now.")).toBeTruthy();

    for (const name of buttons) {
      expect(screen.getByRole("button", { name }).hasAttribute("disabled")).toBe(true);
    }
  });
});
