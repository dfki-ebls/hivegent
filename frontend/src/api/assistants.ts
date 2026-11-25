import { mutationOptions, queryOptions } from "@tanstack/react-query";
import { z } from "zod";

import { jsonRequest, requestJson, requestVoid } from "@/api/client";
import { AGENT_MODES } from "@/api/conversations";
import { FilterEntrySchema } from "@/api/filters";
import { apiUrl } from "@/lib/health";

/** What an assistant is, as the server stores it (see backend `AssistantSpec`). */
const AssistantSpecSchema = z.object({
  description: z.string(),
  instructions: z.string(),
  /** The skills it loads when a conversation starts. */
  skills: z.array(z.string()),
  /** The tools it withholds. */
  disabled_tools: z.array(z.string()),
  included_documents: z.array(FilterEntrySchema),
  excluded_documents: z.array(FilterEntrySchema),
  /** The only paths a chat with it reaches, empty for no restriction. */
  allowed_documents: z.array(FilterEntrySchema),
  /** The mode a run with it may at most take. */
  max_mode: z.enum(AGENT_MODES),
});
type AssistantSpec = z.infer<typeof AssistantSpecSchema>;

/** An assistant the user sees, by its name and the scope that owns it. */
export const AssistantInfoSchema = AssistantSpecSchema.extend({
  name: z.string(),
  /** The reference a chat names it by, such as `~/name`. */
  ref: z.string(),
  scope: z.string(),
  writable: z.boolean(),
});
export type AssistantInfo = z.infer<typeof AssistantInfoSchema>;

function listAssistants(): Promise<AssistantInfo[]> {
  return requestJson(apiUrl("/assistants"), "fetchAssistants", z.array(AssistantInfoSchema));
}

const assistantUrl = (scope: string, name: string) =>
  apiUrl(`/assistants/${encodeURIComponent(scope)}/${encodeURIComponent(name)}`);

/** Save the assistant *name* in *scope* (`~` or `@<group>`), replacing one of its name. */
function saveAssistant({
  scope,
  name,
  spec,
}: {
  scope: string;
  name: string;
  spec: AssistantSpec;
}): Promise<AssistantInfo> {
  return requestJson(
    assistantUrl(scope, name),
    "saveAssistant",
    AssistantInfoSchema,
    jsonRequest("PUT", spec),
  );
}

function deleteAssistant({ scope, name }: AssistantInfo): Promise<void> {
  return requestVoid(assistantUrl(scope, name), "deleteAssistant", { method: "DELETE" });
}

const all = ["assistants"] as const;

export const assistantQueries = {
  /** Every assistant the user sees, their own first, then each group's, then global. */
  list: () => queryOptions({ queryKey: [...all, "list"], queryFn: listAssistants }),
};

export const assistantMutations = {
  save: () =>
    mutationOptions({
      mutationKey: [...all, "save"],
      mutationFn: saveAssistant,
      meta: { invalidates: [all] },
    }),
  delete: () =>
    mutationOptions({
      mutationKey: [...all, "delete"],
      mutationFn: deleteAssistant,
      meta: { invalidates: [all] },
    }),
};
