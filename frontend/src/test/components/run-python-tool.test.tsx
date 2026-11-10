import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunPythonTool } from "@/components/chat/tools/run-python";
import { DocumentDialog } from "@/components/document-dialog";
import { ImageRef } from "@/components/image-ref";
import { WorkspaceImage } from "@/components/workspace-image";
import { AssetMedia } from "@/components/documents/asset-media";
import { fetchDocumentAsset } from "@/api/documents";
import type { ToolPart } from "@/lib/chat/tool-part";
import { createTestChat, renderWithChat } from "@/test/chat";

const { requestText } = vi.hoisted(() => ({
  requestText: vi.fn<(url: string) => Promise<string>>(),
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  requestText,
}));

vi.mock("@/api/documents", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/documents")>()),
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
  render(<RunPythonTool part={finished} metadata={result} prompt={false} />);
  fireEvent.click(screen.getByText(/Run Python/));
}

describe("RunPythonTool", () => {
  it("waits for a persisted conversation before offering temporary source access", () => {
    renderResult(null);

    expect(screen.getByRole("button", { name: "View source" })).toHaveProperty("disabled", true);
  });

  it("loads temporary source with the conversation that owns it", async () => {
    requestText.mockResolvedValue("print(1)");
    renderWithChat(<RunPythonTool part={finished} metadata={null} prompt={false} />);
    fireEvent.click(screen.getByText(/Run Python/));
    fireEvent.click(screen.getByRole("button", { name: "View source" }));

    expect(await screen.findByText("print(1)")).toBeTruthy();
    expect(requestText).toHaveBeenCalledWith(
      expect.stringContaining("/api/documents/%2Ftmp/report.py?conversation_id=c1"),
      "fetchDocumentContent",
    );
  });

  it("lists the changes a program staged and offers its stored source", () => {
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
    expect(screen.getByText("Created ~/summary.md")).toBeTruthy();
    expect(screen.getByText("Moved ~/inbox to ~/archive")).toBeTruthy();
    expect(screen.getByText("Deleted ~/stale.md")).toBeTruthy();
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

    expect(screen.getByText("search(query='a') → []")).toBeTruthy();
    expect(screen.getByText("complete(prompt='b') → failed: RuntimeError: x")).toBeTruthy();
  });

  it.each([false, true])(
    "shows a missing program and error without an empty approval box, approved=%s",
    (approved) => {
      const part: ToolPart = {
        type: "tool-run_python",
        toolCallId: "call-1",
        state: "output-error",
        input: { code: " ", script_path: null },
        errorText: "The program is empty.",
        approval: approved ? { id: "call-1", approved: true } : undefined,
      };

      render(<RunPythonTool part={part} metadata={null} prompt={false} />);
      fireEvent.click(screen.getByText("Run Python"));

      expect(screen.getByText("No Python code or script path was provided.")).toBeTruthy();
      expect(screen.getByText("The program is empty.")).toBeTruthy();

      expect(screen.queryByText("Approved") !== null).toBe(approved);
    },
  );
});

describe("temporary document previews", () => {
  it.each([
    <AssetMedia key="asset" filePath="/tmp/image.png" alt="Preview" />,
    <ImageRef key="reference" src="/tmp/image.png" alt="Preview" />,
    <WorkspaceImage key="markdown" src="image.png" documentPath="/tmp/notes.md" alt="Preview" />,
  ])("loads each image with its owning conversation", async (preview) => {
    vi.stubGlobal(
      "URL",
      class extends URL {
        static override revokeObjectURL = vi.fn<typeof URL.revokeObjectURL>();
      },
    );
    vi.mocked(fetchDocumentAsset).mockResolvedValue("blob:preview");
    vi.mocked(fetchDocumentAsset).mockClear();

    renderWithChat(preview, createTestChat());

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
    requestText.mockResolvedValue("Temporary document contents");

    renderWithChat(
      <DocumentDialog open onOpenChange={() => {}} filename="/tmp/notes.md" initialFullDoc />,
    );

    expect(await screen.findByText("Temporary document contents")).toBeTruthy();
    expect(requestText).toHaveBeenLastCalledWith(
      expect.stringContaining("/api/documents/%2Ftmp/notes.md?conversation_id=c1"),
      "fetchDocumentContent",
    );
  });
});
