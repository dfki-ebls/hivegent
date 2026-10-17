import { act, createRef } from "react";
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ScopeDialogs, type ScopeDialogsHandle } from "@/components/documents/ScopeDialogs";
import { renderWithQueries } from "@/test/render";

describe("ScopeDialogs", () => {
  it.each([
    ["file", "~/reports/notes.md", "notes.md"],
    ["directory", "~/reports/archive", "archive"],
  ] as const)("prefills the current %s name when renaming", (kind, path, name) => {
    const ref = createRef<ScopeDialogsHandle>();
    renderWithQueries(<ScopeDialogs ref={ref} scope="~" onBulkDone={vi.fn<() => void>()} />);

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
