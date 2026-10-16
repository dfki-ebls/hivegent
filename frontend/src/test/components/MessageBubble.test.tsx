import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { MessageBubble } from "@/components/chat/MessageBubble";

it("offers to copy each text part right after it, even when a tool call follows", () => {
  const noop = () => {};

  render(
    <MessageBubble
      message={{
        id: "m1",
        role: "assistant",
        parts: [
          { type: "text", text: "Looking it up." },
          { type: "text", text: "Saved it." },
          {
            type: "tool-save_memory",
            toolCallId: "call-1",
            state: "output-available",
            input: {},
            output: "ok",
          },
        ],
      }}
      isLastMessage
      status="ready"
      editingId={null}
      onSetEditing={noop}
      onCancelEdit={noop}
      onSubmitEdit={noop}
      onRegenerate={noop}
    />,
  );

  const copies = screen.getAllByRole("button", { name: "Copy" });
  const steps = screen.getByRole("button", { name: "Took 1 step" });

  expect(copies).toHaveLength(2);
  expect(copies[1].compareDocumentPosition(steps) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
