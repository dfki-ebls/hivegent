import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RunPythonTool } from "@/components/chat/tools/run-python";
import { ToolApprovalProvider } from "@/hooks/chat/use-tool-approval";
import type { ToolPart } from "@/lib/chat/tool-part";

describe("RunPythonTool", () => {
  it("names the document a program asks to save and offers its stored source", () => {
    const part = {
      type: "tool-run_python",
      toolCallId: "call-1",
      state: "approval-requested",
      input: { script_path: "~/.scratch/report.py", commit_path: "~/report.md" },
      approval: { id: "call-1" },
    } as ToolPart;

    render(
      <ToolApprovalProvider value={{ decide: vi.fn<(id: string, approved: boolean) => void>() }}>
        <RunPythonTool part={part} metadata={null} />
      </ToolApprovalProvider>,
    );

    expect(screen.getByText(/Allow this program to create or replace/)).toBeTruthy();
    expect(screen.getAllByText("~/report.md").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "View source" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
  });
});
