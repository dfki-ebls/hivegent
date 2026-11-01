import { fireEvent, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { SettingsDialog } from "@/components/settings-dialog";
import { clearMemory } from "@/api/account";
import { renderWithQueries } from "@/test/render";

vi.mock("@/api/account", () => ({
  clearMemory: vi.fn<() => Promise<void>>(() => Promise.resolve()),
  clearTmp: vi.fn<() => Promise<void>>(),
}));

vi.mock("@/api/tools", async (importOriginal) => {
  const tools = await importOriginal<typeof import("@/api/tools")>();

  return {
    ...tools,
    toolQueries: {
      ...tools.toolQueries,
      list: () => ({ ...tools.toolQueries.list(), queryFn: () => Promise.resolve([]) }),
    },
    testMcpServer: vi.fn<() => Promise<void>>(),
  };
});

it("clears memory behind a confirmation that closes once confirmed", async () => {
  renderWithQueries(<SettingsDialog />);

  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  fireEvent.click(await screen.findByRole("button", { name: "Clear Memory" }));
  fireEvent.click(await screen.findByRole("button", { name: "Clear" }));

  await waitFor(() => expect(clearMemory).toHaveBeenCalledOnce());
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(screen.getByRole("dialog", { name: "Settings" })).toBeTruthy();
});
