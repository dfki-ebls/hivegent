import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { AssistantDialog } from "@/components/settings/assistant-dialog";
import { renderWithQueries } from "@/test/render";

const { requestJson } = vi.hoisted(() => ({
  requestJson:
    vi.fn<(url: string, label: string, schema: unknown, init?: RequestInit) => Promise<unknown>>(),
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  requestJson,
}));

const SKILL = {
  name: "pdf",
  ref: "/srv/pdf",
  path: "/skills/srv/pdf",
  scope: "/srv",
  description: "Reads PDFs.",
  problem: null,
  warnings: [],
  writable: false,
  model_invocable: true,
  user_invocable: true,
};

it("saves what the form holds, the checked skills included", async () => {
  requestJson.mockImplementation((url) =>
    Promise.resolve(url.endsWith("/skills") ? [SKILL] : url.endsWith("/tools") ? [] : {}),
  );
  renderWithQueries(<AssistantDialog target={{ scope: "~" }} open onOpenChange={() => {}} />);

  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "reader" } });
  fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Read." } });
  const hidden = within(screen.getByRole("group", { name: "Documents to hide" }));
  fireEvent.click(hidden.getByRole("button", { name: "Add path" }));
  fireEvent.change(hidden.getByRole("textbox"), { target: { value: "~/drafts" } });
  fireEvent.click(hidden.getByRole("checkbox", { name: "Directory" }));
  fireEvent.click(hidden.getByRole("button", { name: "Add path" }));
  fireEvent.change(hidden.getByDisplayValue(""), { target: { value: "@team/faq.md" } });
  fireEvent.click(await screen.findByRole("checkbox", { name: "/srv/pdf" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() =>
    expect(requestJson).toHaveBeenCalledWith(
      expect.stringMatching(/\/assistants\/~\/reader$/),
      "saveAssistant",
      expect.anything(),
      expect.objectContaining({ method: "PUT" }),
    ),
  );

  const body = requestJson.mock.calls.find(([, label]) => label === "saveAssistant")?.[3]?.body;

  expect(typeof body === "string" && JSON.parse(body)).toEqual({
    description: "",
    instructions: "Read.",
    skills: ["/srv/pdf"],
    disabled_tools: [],
    included_documents: [],
    excluded_documents: [
      { path: "~/drafts", directory: true },
      { path: "@team/faq.md", directory: false },
    ],
    allowed_documents: [],
    max_mode: "write",
  });
});

it("offers only the tools a list of withheld tools may name", async () => {
  const tool = (name: string, switchable: boolean) => ({
    name,
    description: name,
    group: "documents",
    switchable,
  });
  requestJson.mockImplementation((url) =>
    Promise.resolve(
      url.endsWith("/tools")
        ? [tool("search", true), tool("read_tool_result", false)]
        : url.endsWith("/skills")
          ? []
          : {},
    ),
  );
  renderWithQueries(<AssistantDialog target={{ scope: "~" }} open onOpenChange={() => {}} />);

  expect(await screen.findByRole("checkbox", { name: "search" })).toBeDefined();
  expect(screen.queryByRole("checkbox", { name: "read_tool_result" })).toBeNull();
});
