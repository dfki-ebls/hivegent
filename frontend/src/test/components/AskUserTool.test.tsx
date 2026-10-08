import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AskUserTool } from "@/components/chat/tools/ask-user";
import { ToolApprovalProvider, type ToolApprovalGate } from "@/hooks/chat/use-tool-approval";
import { DISMISSED_ERROR } from "@/lib/chat/ask-user";

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

function renderQuestions() {
  const addToolOutput = vi.fn<ToolApprovalGate["addToolOutput"]>();

  render(
    <ToolApprovalProvider value={{ decide: () => {}, addToolOutput }}>
      <AskUserTool
        part={{
          type: "tool-ask_user",
          toolCallId: "ask-1",
          state: "input-available",
          input: { questions },
        }}
      />
    </ToolApprovalProvider>,
  );

  return addToolOutput;
}

describe("ask user", () => {
  it("submits one answer per question, with typed answers and notes", () => {
    const addToolOutput = renderQuestions();
    const submit = screen.getByRole("button", { name: "Submit answers" });

    fireEvent.click(screen.getByRole("radio", { name: /All/ }));
    fireEvent.change(screen.getByLabelText("Note for the assistant (optional)"), {
      target: { value: " Skip drafts. " },
    });
    expect(submit).toHaveProperty("disabled", true);

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Format" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "PDF" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Other" }));
    fireEvent.change(screen.getByLabelText("Other"), { target: { value: "HTML" } });
    fireEvent.click(submit);

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
      [{ tool: "ask_user", toolCallId: "ask-1", state: "output-error", errorText: DISMISSED_ERROR }],
    ]);
  });
});
