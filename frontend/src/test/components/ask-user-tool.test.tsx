import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MessageLiveProvider } from "@/components/chat/chat-context";
import { AskUserTool } from "@/components/chat/tools/ask-user";
import { DISMISSED_ERROR } from "@/lib/chat/ask-user";
import { createTestChat, renderWithChat } from "@/test/chat";

const questions = [
  {
    header: "Scope",
    question: "Which scope?",
    options: [{ label: "All", recommended: true }, { label: "Some" }],
  },
  {
    header: "Format",
    question: "Which formats?",
    options: [{ label: "PDF" }, { label: "Markdown" }],
    multi_select: true,
  },
];

/** The questions of the last message while the chat waits for their answers. */
function renderQuestions() {
  const chat = createTestChat();
  const addToolOutput = vi.spyOn(chat, "addToolOutput").mockResolvedValue();

  renderWithChat(
    <MessageLiveProvider value="waiting">
      <AskUserTool
        metadata={null}
        prompt
        part={{
          type: "tool-ask_user",
          toolCallId: "ask-1",
          state: "input-available",
          input: { questions },
        }}
      />
    </MessageLiveProvider>,
    chat,
  );

  return addToolOutput;
}

describe("ask user", () => {
  it("submits one answer per question, with typed answers and notes", () => {
    const addToolOutput = renderQuestions();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("alert").textContent).toBe("Choose or type an answer to continue.");

    fireEvent.click(screen.getByRole("radio", { name: /All/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note for the assistant (optional)" }), {
      target: { value: " Skip drafts. " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    fireEvent.click(screen.getByRole("checkbox", { name: "PDF" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Other" }), {
      target: { value: " HTML " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit answers" }));

    expect(addToolOutput.mock.calls).toEqual([
      [
        {
          tool: "ask_user",
          toolCallId: "ask-1",
          output: [
            { selected: ["All"], note: "Skip drafts." },
            { selected: ["PDF"], other: "HTML" },
          ],
        },
      ],
    ]);
  });

  it("dismisses the questions without answers", () => {
    const addToolOutput = renderQuestions();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(addToolOutput.mock.calls).toEqual([
      [
        {
          tool: "ask_user",
          toolCallId: "ask-1",
          state: "output-error",
          errorText: DISMISSED_ERROR,
        },
      ],
    ]);
  });
});
