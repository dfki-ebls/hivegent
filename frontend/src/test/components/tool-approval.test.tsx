import { act, fireEvent, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { MessageBubble } from "@/components/chat/message-bubble";
import type { ChatMessage } from "@/lib/chat/message";
import { createTestChat, renderWithChat, startRun } from "@/test/chat";

vi.mock("@/oidc", () => ({ useOidc: () => ({ isUserLoggedIn: false }) }));

type Part = ChatMessage["parts"][number];

const request = (id: string): Part => ({
  type: "tool-send_mail",
  toolCallId: id,
  state: "approval-requested",
  input: { to: id },
  approval: { id },
});

const approved = (id: string): Part => ({
  type: "tool-send_mail",
  toolCallId: id,
  state: "output-available",
  input: { to: id },
  output: "sent",
  approval: { id, approved: true },
});

const summaryOf = (overrides: Record<string, unknown>) => ({
  creates: [],
  updates: [],
  moves: [],
  deletes: [],
  mkdirs: [],
  reasons: [],
  ...overrides,
});

const NOTE = "Instruction for the assistant on Send Mail (optional)";
const SHARED_NOTE = "Instruction for all actions (optional)";

interface RenderOptions {
  approvalMetadata?: Record<string, unknown>;
  approvalArgs?: Record<string, Record<string, unknown>>;
  part?: (id: string) => Part;
}

/** The last message of a chat that waits for the user, with a call of *ids* each. */
function renderMessage(
  ids: string[],
  { approvalMetadata, approvalArgs, part = request }: RenderOptions = {},
) {
  const messageOf = (requests: string[]): ChatMessage => ({
    id: "m1",
    role: "assistant",
    parts: requests.map(part),
    metadata: { approvalMetadata, approvalArgs },
  });
  const chat = createTestChat([messageOf(ids)]);
  const decide = vi.spyOn(chat, "addToolApprovalResponse").mockResolvedValue();
  const { rerender } = renderWithChat(<MessageBubble message={messageOf(ids)} />, chat);

  return {
    chat,
    decide,
    decisions: () => decide.mock.calls.map(([decision]) => decision),
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

const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

it("sends a lone request from its card at once, with the note", () => {
  const { decisions, card } = renderMessage(["call-1"]);

  expect(screen.queryByRole("button", { name: "Approve all" })).toBeNull();
  type(NOTE, " Wrong folder. ");
  fireEvent.click(card(0).getByRole("button", { name: "Deny" }));

  expect(decisions()).toEqual([{ id: "call-1", approved: false, reason: "Wrong folder." }]);
});

it("stages the card choices of several requests and sends them on submit", () => {
  const { decisions, card } = renderMessage(["call-1", "call-2"]);

  fireEvent.click(card(0).getByRole("button", { name: "Approve" }));
  fireEvent.click(card(1).getByRole("button", { name: "Approve" }));
  fireEvent.click(card(1).getByRole("button", { name: "Deny" }));
  fireEvent.change(card(1).getByLabelText(NOTE), { target: { value: "Not this one." } });

  expect(decisions()).toEqual([]);
  expect(screen.getByText("2 of 2 decided")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Submit decisions" }));

  expect(decisions()).toEqual([
    { id: "call-1", approved: true },
    { id: "call-2", approved: false, reason: "Not this one." },
  ]);
});

it("answers several requests with the shared note, kept as requests arrive and blank next round", () => {
  const { decisions, decide, rerender } = renderMessage(["call-1", "call-2"]);

  type(SHARED_NOTE, "Keep backups.");
  rerender(["call-0", "call-1", "call-2"]);
  fireEvent.click(screen.getByRole("button", { name: "Approve all" }));

  expect(decisions()).toEqual(
    ["call-0", "call-1", "call-2"].map((id) => ({ id, approved: true, reason: "Keep backups." })),
  );
  decide.mockClear();

  rerender(["call-4", "call-5"]);

  expect(screen.getByLabelText(SHARED_NOTE)).toHaveProperty("value", "");

  fireEvent.click(screen.getByRole("button", { name: "Deny all" }));

  expect(decisions()).toEqual([
    { id: "call-4", approved: false },
    { id: "call-5", approved: false },
  ]);
});

it("asks with the changeset summary and planner reasons, and the arguments the call runs with", () => {
  const summary = summaryOf({
    creates: [{ path: "~/out.md", diff: "" }],
    reasons: [{ action: "remove", key: 2, reason: "A duplicate of case 1." }],
  });
  const { card } = renderMessage(["call-1"], {
    approvalMetadata: { "call-1": summary },
    approvalArgs: { "call-1": { to: "repaired@example.org" } },
  });
  const { textContent } = screen.getByRole("group", { name: /^Allow the assistant/ });

  expect(card(0).getByText("Created ~/out.md")).toBeTruthy();
  expect(card(0).getByText("Remove case 2")).toBeTruthy();
  expect(card(0).getByText("A duplicate of case 1.")).toBeTruthy();
  expect(textContent).toContain("repaired@example.org");
  expect(textContent).not.toContain("call-1");
});

it("keeps showing the changes once the call is resolved", () => {
  renderMessage(["call-1"], {
    approvalMetadata: { "call-1": summaryOf({ deletes: ["~/old.md"] }) },
    part: approved,
  });
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
])("blocks answering %j while streaming, with the reason", async (ids, buttons) => {
  const { chat } = renderMessage(ids);

  await startRun(chat);

  expect(screen.getByText("Available once the current response finishes.")).toBeTruthy();

  for (const name of buttons) {
    expect(screen.getByRole("button", { name }).hasAttribute("disabled")).toBe(true);
  }
});
