import { screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { MessageBubble } from "@/components/chat/message-bubble";
import type { ChatMessage } from "@/lib/chat/message";
import { createTestChat, renderWithChat } from "@/test/chat";

vi.mock("@/oidc", () => ({ useOidc: () => ({ isUserLoggedIn: false }) }));

it("offers to copy each text part right after it, even when a tool call follows", () => {
  const message: ChatMessage = {
    id: "m1",
    role: "assistant",
    parts: [
      { type: "text", text: "Looking it up." },
      { type: "text", text: "Saved it." },
      {
        type: "tool-write_memory",
        toolCallId: "call-1",
        state: "output-available",
        input: {},
        output: "ok",
      },
    ],
  };

  renderWithChat(<MessageBubble message={message} />, createTestChat([message]));

  const copies = screen.getAllByRole("button", { name: "Copy" });
  const steps = screen.getByRole("button", { name: "Took 1 step" });

  expect(copies).toHaveLength(2);
  expect(copies[1]?.compareDocumentPosition(steps)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
});
