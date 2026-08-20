import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Citation } from "@/components/Citation";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";

vi.mock("@/components/DocumentDialog", () => ({
  DocumentDialog: ({ open }: { open: boolean }) => (open ? <div>Current document</div> : null),
}));

describe("Citation", () => {
  beforeEach(() => {
    useFetchedDocumentsStore.getState().clearAll();
  });

  it("opens line evidence captured in a tool output", () => {
    useFetchedDocumentsStore.getState().addChunk({
      filename: "~/report.md",
      content: "alpha\nbeta\ngamma",
      origin: "read",
      position: { type: "line_range", startLine: 1, endLine: 3 },
      sourceId: "call-1",
    });

    render(
      <TooltipProvider>
        <Citation src="~/report.md" line="2" />
      </TooltipProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Line 2" }));

    expect(screen.getByText("beta")).toBeTruthy();
    expect(screen.getByText("Captured by read")).toBeTruthy();
  });

  it("disables a line with no supporting tool output", () => {
    render(
      <TooltipProvider>
        <Citation src="~/report.md" line="8" />
      </TooltipProvider>,
    );

    expect(screen.getByRole("button", { name: "Line 8" }).hasAttribute("disabled")).toBe(true);
  });
});
