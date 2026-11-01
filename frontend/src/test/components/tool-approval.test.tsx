import { act, fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MessageBubble } from "@/components/chat/message-bubble";
import type { ChatMessage } from "@/lib/chat/message";
import { createTestChat, renderWithChat, startRun } from "@/test/chat";

vi.mock("@/oidc", () => ({ useOidc: () => ({ isUserLoggedIn: false }) }));

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
  approvalMetadata?: Record<string, unknown>;
  part?: (id: string) => ChatMessage["parts"][number];
}

/** The last message of a chat that waits for the user, with a call of *ids* each. */
function renderMessage(ids: string[], { approvalMetadata, part = request }: RenderOptions = {}) {
  const messageOf = (requests: string[]): ChatMessage => ({
    id: "m1",
    role: "assistant",
    parts: requests.map(part),
    metadata: { approvalMetadata },
  });
  const chat = createTestChat([messageOf(ids)]);
  const decide = vi.spyOn(chat, "addToolApprovalResponse").mockResolvedValue();
  const { rerender } = renderWithChat(<MessageBubble message={messageOf(ids)} />, chat);

  return {
    chat,
    decide,
    card: (index: number) => {
      const card = screen.getAllByRole("group", { name: /^Allow the assistant/ })[index];

      if (!card) throw new Error(`No card ${index}`);

      return within(card);
    },
    rerender: (requests: string[]) => {
      const message = messageOf(requests);

      act(() => {
        chat.messages = [message];
      });
      rerender(<MessageBubble message={message} />);
    },
  };
}

describe("tool approval", () => {
  it("sends a lone request from its card at once, with the note", () => {
    const { decide, card } = renderMessage(["call-1"]);

    expect(screen.queryByRole("button", { name: "Approve all" })).toBeNull();
    fireEvent.change(
      card(0).getByLabelText("Instruction for the assistant on Send Mail (optional)"),
      {
        target: { value: " Wrong folder. " },
      },
    );
    fireEvent.click(card(0).getByRole("button", { name: "Deny" }));

    expect(decide.mock.calls).toEqual([[{ id: "call-1", approved: false, reason: "Wrong folder." }]]);
  });

  it("stages the card choices of several requests and sends them on submit", () => {
    const { decide, card } = renderMessage(["call-1", "call-2"]);
    const submit = screen.getByRole("button", { name: "Submit decisions" });

    fireEvent.click(card(0).getByRole("button", { name: "Approve" }));
    fireEvent.click(card(1).getByRole("button", { name: "Approve" }));
    fireEvent.click(card(1).getByRole("button", { name: "Deny" }));
    fireEvent.change(
      card(1).getByLabelText("Instruction for the assistant on Send Mail (optional)"),
      {
        target: { value: "Not this one." },
      },
    );

    expect(decide).not.toHaveBeenCalled();
    expect(card(1).getByRole("button", { name: "Deny", pressed: true })).toBeTruthy();
    expect(screen.getByText("2 of 2 decided")).toBeTruthy();
    fireEvent.click(submit);

    expect(decide.mock.calls).toEqual([
      [{ id: "call-1", approved: true }],
      [{ id: "call-2", approved: false, reason: "Not this one." }],
    ]);
  });

  it("answers several requests at once with the shared note", () => {
    const { decide } = renderMessage(["call-1", "call-2"]);

    fireEvent.change(screen.getByLabelText("Instruction for all actions (optional)"), {
      target: { value: "Keep backups." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Approve all" }));

    expect(decide.mock.calls).toEqual([
      [{ id: "call-1", approved: true, reason: "Keep backups." }],
      [{ id: "call-2", approved: true, reason: "Keep backups." }],
    ]);
  });

  it("keeps a shared note as requests arrive and starts the next round blank", () => {
    const { decide, rerender } = renderMessage(["call-1", "call-2"]);

    fireEvent.change(screen.getByLabelText("Instruction for all actions (optional)"), {
      target: { value: "Keep backups." },
    });
    rerender(["call-1", "call-2", "call-3"]);
    rerender(["call-0", "call-1", "call-2", "call-3"]);
    fireEvent.click(screen.getByRole("button", { name: "Approve all" }));

    expect(decide.mock.calls.map(([decision]) => decision)).toEqual(
      ["call-0", "call-1", "call-2", "call-3"].map((id) => ({
        id,
        approved: true,
        reason: "Keep backups.",
      })),
    );
    decide.mockClear();

    rerender(["call-4", "call-5"]);
    expect(screen.getByLabelText("Instruction for all actions (optional)")).toHaveProperty(
      "value",
      "",
    );
    fireEvent.click(screen.getByRole("button", { name: "Deny all" }));

    expect(decide.mock.calls).toEqual([
      [{ id: "call-4", approved: false }],
      [{ id: "call-5", approved: false }],
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
    expect(card(0).getByText("Created ~/out.md")).toBeTruthy();
  });

  it("keeps showing the changes once the call is resolved", () => {
    const summary = {
      creates: [],
      updates: [],
      moves: [],
      deletes: ["~/old.md"],
      mkdirs: [],
    };
    renderMessage(["call-1"], {
      approvalMetadata: { "call-1": summary },
      part: approved,
    });
    // A resolved call renders collapsed, inside the steps that ran it.
    fireEvent.click(screen.getByRole("button", { name: "Took 1 step" }));
    fireEvent.click(screen.getByRole("button", { name: /Send Mail/ }));

    expect(screen.getByText("Approved")).toBeTruthy();
    expect(screen.getByText("Deleted ~/old.md")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  // A decision made while the last chunks drain is never dispatched by the SDK.
  it.each([
    [["call-1"], ["Approve", "Deny"]],
    [
      ["call-1", "call-2"],
      ["Approve all", "Deny all", "Submit decisions"],
    ],
  ])("blocks sending %j while streaming, with the reason", async (ids, buttons) => {
    const { chat } = renderMessage(ids);

    await startRun(chat);

    expect(screen.getByText("Available once the current response finishes.")).toBeTruthy();

    for (const name of buttons) {
      expect(screen.getByRole("button", { name }).hasAttribute("disabled")).toBe(true);
    }
  });
});
