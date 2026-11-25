import { mutationOptions, queryOptions } from "@tanstack/react-query";
import { z } from "zod";

import { requestJson, requestVoid } from "@/api/client";
import { apiUrl } from "@/lib/health";

/** A skill the user sees, valid or with the reason it is not. */
const SkillInfoSchema = z.object({
  name: z.string(),
  /** The reference it is loaded and invoked by, such as `~/name`. */
  ref: z.string(),
  path: z.string(),
  scope: z.string(),
  description: z.string().nullable(),
  problem: z.string().nullable(),
  warnings: z.array(z.string()),
  writable: z.boolean(),
  /** Whether the model may load it, false for `disable-model-invocation: true`. */
  model_invocable: z.boolean(),
  /** Whether a prompt may invoke it by `/<ref>`, false for an invalid one and `user-invocable: false`. */
  user_invocable: z.boolean(),
});
export type SkillInfo = z.infer<typeof SkillInfoSchema>;

const SkillInstalledSchema = z.object({
  skill: SkillInfoSchema,
  scripts: z.array(
    z.object({
      path: z.string(),
      signature: z.string().nullable(),
      problem: z.string().nullable(),
    }),
  ),
});
type SkillInstalled = z.infer<typeof SkillInstalledSchema>;

/** Whether a skill loads, which only a valid one does. */
export const isUsableSkill = (skill: SkillInfo): boolean => skill.problem === null;

/** The word a prompt invokes a skill by after its slash, its reference without a leading slash. */
export const skillCommand = (skill: SkillInfo): string => skill.ref.replace(/^\//, "");

/** The file a skill is opened by in the document viewer. */
export const skillFile = (skill: SkillInfo): string => `${skill.path}/SKILL.md`;

function listSkills(): Promise<SkillInfo[]> {
  return requestJson(apiUrl("/skills"), "fetchSkills", z.array(SkillInfoSchema));
}

/** Install the skill a ZIP archive holds in the library of *scope* (`~` or `@<group>`). */
function installSkill({
  scope,
  archive,
}: {
  scope: string;
  archive: File;
}): Promise<SkillInstalled> {
  const body = new FormData();
  body.append("file", archive);

  return requestJson(
    apiUrl(`/skills/${encodeURIComponent(scope)}`),
    "installSkill",
    SkillInstalledSchema,
    { method: "PUT", body },
  );
}

function deleteSkill(skill: SkillInfo): Promise<void> {
  return requestVoid(
    apiUrl(`/skills/${encodeURIComponent(skill.scope)}/${encodeURIComponent(skill.name)}`),
    "deleteSkill",
    { method: "DELETE" },
  );
}

const all = ["skills"] as const;

export const skillQueries = {
  /** Every skill the user sees, their own first, then each group's, then global. */
  list: () => queryOptions({ queryKey: [...all, "list"], queryFn: listSkills }),
};

export const skillMutations = {
  install: () =>
    mutationOptions({
      mutationKey: [...all, "install"],
      mutationFn: installSkill,
      meta: { invalidates: [all] },
    }),
  delete: () =>
    mutationOptions({
      mutationKey: [...all, "delete"],
      mutationFn: deleteSkill,
      meta: { invalidates: [all] },
    }),
};
