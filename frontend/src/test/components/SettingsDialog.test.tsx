import { fireEvent, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { SettingsDialog } from "@/components/SettingsDialog";
import { clearMemory } from "@/lib/api";
import { renderWithQueries } from "@/test/render";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  clearMemory: vi.fn<() => Promise<void>>(() => Promise.resolve()),
  clearTmp: vi.fn<() => Promise<void>>(),
  listTools: vi.fn<() => Promise<never[]>>(() => Promise.resolve([])),
  testMcpServer: vi.fn<() => Promise<void>>(),
}));

it("clears memory behind a confirmation that closes once confirmed", async () => {
  renderWithQueries(<SettingsDialog />);

  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  fireEvent.click(await screen.findByRole("button", { name: "Clear Memory" }));
  fireEvent.click(await screen.findByRole("button", { name: "Clear" }));

  await waitFor(() => expect(clearMemory).toHaveBeenCalledOnce());
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(screen.getByRole("dialog", { name: "Settings" })).toBeTruthy();
});
