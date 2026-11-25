import { fireEvent, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import type { SkillInfo } from "@/api/skills";
import { SkillsSection } from "@/components/skills-section";
import { renderWithQueries } from "@/test/render";

const { requestVoid, skill } = vi.hoisted(() => ({
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
