import { fireEvent, screen, waitFor } from "@testing-library/react";
import { TrashIcon } from "lucide-react";
import { expect, it, vi } from "vitest";

import { DangerZone } from "@/components/settings/danger-zone";
import { renderWithQueries } from "@/test/render";

it("runs an action behind a confirmation that closes once confirmed", async () => {
  const run = vi.fn<() => Promise<void>>(() => Promise.resolve());

  renderWithQueries(
    <DangerZone
      title="Zone"
      description="Careful"
      entries={[
        {
          key: "x",
          icon: TrashIcon,
          title: "Clear Memory",
          confirm: "Clear",
          description: "",
          run,
        },
      ]}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  fireEvent.click(await screen.findByRole("button", { name: "Clear" }));

  await waitFor(() => expect(run).toHaveBeenCalledOnce());
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
});
