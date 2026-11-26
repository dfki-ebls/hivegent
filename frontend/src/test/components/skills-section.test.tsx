import { fireEvent, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import type { SkillInfo } from "@/api/skills";
import { SkillsSection } from "@/components/skills-section";
import { renderWithQueries } from "@/test/render";

const { requestJson, requestVoid, skill } = vi.hoisted(() => ({
  requestJson: vi.fn<
    (url: string, label: string, schema: unknown, init?: RequestInit) => Promise<unknown>
  >(() => Promise.resolve({ name: "csv-check" })),
  requestVoid: vi.fn<(url: string) => Promise<void>>(() => Promise.resolve()),
  skill: (name: string, scope: string, extra: Partial<SkillInfo> = {}): SkillInfo => ({
    name,
    ref: `${scope}/${name}`,
    path: `/skills/${scope.replace(/^\//, "")}/${name}`,
    scope,
    description: `Does ${name}.`,
    problem: null,
    warnings: [],
    writable: scope === "~",
    model_invocable: true,
    user_invocable: true,
    ...extra,
  }),
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  requestJson,
  requestVoid,
}));

vi.mock("@/hooks/use-workspaces", () => ({
  useWorkspaces: () => [
    { scope: "/srv", kind: "global", label: "Organisation", writable: true },
    { scope: "~", kind: "user", label: "Me", writable: true },
  ],
}));

vi.mock("@/api/skills", async (importOriginal) => {
  const skills = await importOriginal<typeof import("@/api/skills")>();
  const listed = [
    skill("pdf", "/srv"),
    skill("count", "~", { description: null, problem: "The folder holds no SKILL.md." }),
  ];

  return {
    ...skills,
    skillQueries: {
      list: () => ({ ...skills.skillQueries.list(), queryFn: () => Promise.resolve(listed) }),
    },
  };
});

it("lists skills by scope, uploads only to a library, and deletes behind a confirmation", async () => {
  renderWithQueries(<SkillsSection />);

  expect(await screen.findByText("Does pdf.")).toBeTruthy();
  expect(screen.getByText("The folder holds no SKILL.md.")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "Upload ZIP" })).toHaveLength(1);

  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));

  await waitFor(() =>
    expect(requestVoid).toHaveBeenCalledExactlyOnceWith(
      expect.stringMatching(/\/skills\/~\/count$/),
      "deleteSkill",
      { method: "DELETE" },
    ),
  );
});

it("writes a new skill whose SKILL.md follows its name", async () => {
  renderWithQueries(<SkillsSection />);

  fireEvent.click(await screen.findByRole("button", { name: "New Skill" }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "csv-check" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() =>
    expect(requestJson).toHaveBeenCalledWith(
      expect.stringMatching(/\/skills\/~\/csv-check$/),
      "saveSkillDocument",
      expect.anything(),
      expect.objectContaining({ method: "POST" }),
    ),
  );
  const body = requestJson.mock.calls.find(([, label]) => label === "saveSkillDocument")?.[3]?.body;
  expect(JSON.parse(typeof body === "string" ? body : "{}").content).toContain("name: csv-check\n");
});
