import { act, createRef } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  ScopeDialogs,
  type ScopeDialogsHandle,
} from "@/components/documents/ScopeDialogs";

vi.mock("@/stores/documents-store", () => {
  const store = {
    deleteDir: vi.fn<() => void>(),
    remove: vi.fn<() => void>(),
    bulkDelete: vi.fn<() => void>(),
    move: vi.fn<() => void>(),
    moveDir: vi.fn<() => void>(),
  };

  return {
    useDocumentsStore: (selector: (state: typeof store) => unknown) => selector(store),
  };
});

describe("ScopeDialogs", () => {
  it.each([
    ["file", "~/reports/notes.md", "notes.md"],
    ["directory", "~/reports/archive", "archive"],
  ] as const)("prefills the current %s name when renaming", (kind, path, name) => {
    const ref = createRef<ScopeDialogsHandle>();
    render(<ScopeDialogs ref={ref} scope="~" onBulkDone={vi.fn<() => void>()} />);

    act(() => {
      if (kind === "file") {
        ref.current?.renameFile(path);
      } else {
        ref.current?.renameDir(path);
      }
    });

    const input = screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement;
    expect(input.value).toBe(name);
  });
});
