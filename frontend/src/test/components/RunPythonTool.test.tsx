import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunPythonTool } from "@/components/chat/tools/run-python";
import { DocumentDialog } from "@/components/DocumentDialog";
import { ImageRef } from "@/components/ImageRef";
import { WorkspaceImage } from "@/components/WorkspaceImage";
import { AssetImage } from "@/components/documents/AssetImage";
import { ConversationIdProvider } from "@/hooks/chat/use-conversation-id";
import { fetchDocumentAsset, getDocumentContent } from "@/lib/api";
import type { ToolPart } from "@/lib/chat/tool-part";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getDocumentContent: vi.fn<typeof getDocumentContent>(),
  fetchDocumentAsset: vi.fn<typeof fetchDocumentAsset>(),
}));

vi.mock("@/hooks/use-in-view", () => ({ useInView: () => [() => {}, true] }));

afterEach(() => vi.unstubAllGlobals());

const finished: ToolPart = {
  type: "tool-run_python",
  toolCallId: "call-1",
  state: "output-available",
  input: { script_path: "/tmp/report.py" },
  output: "",
};

function renderResult(changeset: unknown, calls: unknown[] = []) {
  const result = { result: null, stdout: "", script_path: null, changeset, calls };
  render(<RunPythonTool part={finished} metadata={result} />);
  fireEvent.click(screen.getByText(/Run Python/));
}

describe("RunPythonTool", () => {
  it("waits for a persisted conversation before offering temporary source access", () => {
    renderResult(null);

    expect(screen.getByRole("button", { name: "View source" })).toHaveProperty("disabled", true);
  });

  it("loads temporary source with the conversation that owns it", async () => {
    vi.mocked(getDocumentContent).mockResolvedValue("print(1)");
    render(
      <ConversationIdProvider value="c1">
        <RunPythonTool part={finished} metadata={null} />
      </ConversationIdProvider>,
    );
    fireEvent.click(screen.getByText(/Run Python/));
    fireEvent.click(screen.getByRole("button", { name: "View source" }));

    expect(await screen.findByText("print(1)")).toBeTruthy();
    expect(getDocumentContent).toHaveBeenCalledWith("/tmp/report.py", "c1");
  });

  it("lists the changes a program staged, marking folders, and offers its stored source", () => {
    renderResult({
      status: "pending",
      changeset_id: "abc",
      summary: {
        creates: [{ path: "~/summary.md", diff: "--- /dev/null\n+++ ~/summary.md\n+done\n" }],
        updates: [],
        moves: [{ source: "~/inbox", destination: "~/archive", is_dir: true, replaces: false }],
        deletes: ["~/stale.md"],
        mkdirs: [],
      },
    });

    expect(screen.getByText(/Staged 3 changes/)).toBeTruthy();
    expect(screen.getByText("~/archive")).toBeTruthy();
    expect(screen.getByText("Folder")).toBeTruthy();
    expect(screen.getByText("~/stale.md")).toBeTruthy();
    expect(screen.getByRole("button", { name: "View source" })).toBeTruthy();
  });

  it("reports the changes a program applied at once", () => {
    renderResult({ status: "applied", reports: ["Wrote /tmp/a.md", "Deleted /tmp/b.md"] });

    expect(screen.getByText(/Applied 2 changes/)).toBeTruthy();
    expect(screen.getByText(/Deleted \/tmp\/b\.md/)).toBeTruthy();
  });

  it("lists the host functions a program called", () => {
    renderResult(null, [
      { function: "search", arguments: "query='a'", result: "[]", error: null },
      { function: "complete", arguments: "prompt='b'", result: null, error: "RuntimeError: x" },
    ]);

    fireEvent.click(screen.getByText("2 function calls"));

    expect(screen.getByText(/search\(query='a'\) → \[\]\s+complete\(prompt='b'\) → failed: RuntimeError: x/)).toBeTruthy();
  });

  it.each([false, true])(
    "shows a missing program and error without an empty approval box, approved=%s",
    (approved) => {
      const part: ToolPart = {
        type: "tool-run_python",
        toolCallId: "call-1",
        state: "output-error",
        input: undefined,
        rawInput: JSON.stringify({ code: " ", script_path: null }),
        errorText: "The program is empty.",
        approval: approved ? { id: "call-1", approved: true } : undefined,
      };

      render(<RunPythonTool part={part} metadata={null} />);
      fireEvent.click(screen.getByText("Run Python"));

      expect(screen.getByText("No Python code or script path was provided.")).toBeTruthy();
      expect(screen.getByText("The program is empty.")).toBeTruthy();

      expect(screen.queryByRole("alert")?.textContent ?? null).toBe(approved ? "Approved" : null);
    },
  );
});

describe("temporary document previews", () => {
  it.each([
    <AssetImage key="asset" filePath="/tmp/image.png" alt="Preview" />,
    <ImageRef key="reference" src="/tmp/image.png" alt="Preview" />,
    <WorkspaceImage key="markdown" src="image.png" documentPath="/tmp/notes.md" alt="Preview" />,
  ])("loads each image with its owning conversation", async (preview) => {
    vi.stubGlobal(
      "URL",
      class extends URL {
        static revokeObjectURL = vi.fn<typeof URL.revokeObjectURL>();
      },
    );
    vi.mocked(fetchDocumentAsset).mockResolvedValue("blob:preview");
    vi.mocked(fetchDocumentAsset).mockClear();

    render(<ConversationIdProvider value="c1">{preview}</ConversationIdProvider>);

    await waitFor(() =>
      expect(fetchDocumentAsset).toHaveBeenLastCalledWith(
        "/tmp/image.png",
        expect.any(AbortSignal),
        "c1",
      ),
    );
    expect((await screen.findByRole("img", { name: "Preview" })).getAttribute("src")).toBe(
      "blob:preview",
    );
  });

  it("loads complete temporary text with its owning conversation", async () => {
    vi.mocked(getDocumentContent).mockResolvedValue("Temporary document contents");

    render(
      <ConversationIdProvider value="c1">
        <DocumentDialog open onOpenChange={() => {}} filename="/tmp/notes.md" initialFullDoc />
      </ConversationIdProvider>,
    );

    expect(await screen.findByText("Temporary document contents")).toBeTruthy();
    expect(getDocumentContent).toHaveBeenLastCalledWith("/tmp/notes.md", "c1");
  });
});
