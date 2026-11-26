import { describe, expect, it } from "vitest";
import { type ZodType, z } from "zod";

import { EXPORT_PARTS } from "@/api/account";
import { DataIntegrationSchema, McpIntegrationSchema, PluginInfoSchema } from "@/api/admin";
import { AssistantInfoSchema } from "@/api/assistants";
import { CaseBaseSchema } from "@/api/case-bases";
import { AGENT_MODES, REASONING_EFFORTS } from "@/api/conversations";
import { ChunkedDocumentResponseSchema, DirectoryTreeResponseSchema } from "@/api/documents";
import { FeedEventSchema } from "@/api/jobs";
import { BackendSettingsSchema } from "@/api/settings";
import { TransparencyDetectionResponseSchema } from "@/api/transparency";
import { TOOL_ICONS } from "@/components/chat/tools/icons";
import { ChangesetOutcomeSchema } from "@/components/chat/tools/run-python";
import { WriteInputSchema } from "@/components/chat/tools/write-document";
import { chat as de } from "@/i18n/locales/de/chat";
import { chat as en } from "@/i18n/locales/en/chat";
import { SubagentUpdateSchema } from "@/lib/chat/subagent";

type JsonSchema = z.core.JSONSchema.JSONSchema;

/** The contract `backend/tests/unit/test_contract.py` records from the backend. */
interface Contract {
  schemas: Record<string, JsonSchema>;
  tools: string[];
}

const [contract] = Object.values(
  import.meta.glob<Contract>("../fixtures/contract.json", { eager: true, import: "default" }),
);

if (!contract) throw new Error("contract.json is missing");

/** The enum and constant values of *schema* by property path, union branches merged. */
function enumsOf(
  schema: JsonSchema,
  resolve: (ref: string) => JsonSchema | undefined,
): Map<string, Set<unknown>> {
  const found = new Map<string, Set<unknown>>();

  function walk(
    node: z.core.JSONSchema._JSONSchema | undefined,
    path: string,
    seen: ReadonlySet<string>,
  ): void {
    if (node === undefined || typeof node === "boolean") return;

    if (node.$ref !== undefined) {
      if (seen.has(node.$ref)) return;

      const target = resolve(node.$ref);

      if (!target) throw new Error(`Unresolved ${node.$ref}`);

      walk(target, path, new Set([...seen, node.$ref]));

      return;
    }

    const values = node.enum ?? (node.const === undefined ? [] : [node.const]);

    if (values.length > 0) {
      const set = found.get(path) ?? new Set();
      found.set(path, set);
      for (const value of values) if (value !== null) set.add(value);
    }

    for (const [key, child] of Object.entries(node.properties ?? {}))
      walk(child, `${path}.${key}`, seen);

    for (const item of [node.items ?? []].flat()) walk(item, `${path}[]`, seen);

    walk(node.additionalProperties, `${path}{}`, seen);

    for (const child of [...(node.anyOf ?? []), ...(node.oneOf ?? []), ...(node.allOf ?? [])]) {
      walk(child, path, seen);
    }
  }

  walk(schema, "", new Set());

  return found;
}

function backendEnums(name: string): Map<string, Set<unknown>> {
  const schema = contract?.schemas[name];

  if (!schema) throw new Error(`The backend has no schema ${name}`);

  return enumsOf(schema, (ref) => contract?.schemas[ref.replace("#/components/schemas/", "")]);
}

function frontendEnums(schema: ZodType): Map<string, Set<unknown>> {
  const json = z.toJSONSchema(schema, { io: "input", unrepresentable: "any" });

  return enumsOf(json, (ref) => json.$defs?.[ref.replace("#/$defs/", "")]);
}

/** Each zod schema of a wire shape, by the backend schema it mirrors. */
const MIRRORS: [string, ZodType][] = [
  ["AssistantInfo", AssistantInfoSchema],
  ["CaseBaseResponse", CaseBaseSchema],
  ["ChangesetOutcome", ChangesetOutcomeSchema],
  ["DataIntegration", DataIntegrationSchema],
  ["DirectoryTreeResponse", DirectoryTreeResponseSchema],
  ["DocumentChunksResponse", ChunkedDocumentResponseSchema],
  ["ExportPart", z.enum(EXPORT_PARTS)],
  ["FeedEvent", FeedEventSchema],
  ["McpIntegration", McpIntegrationSchema],
  ["Mode", z.enum(AGENT_MODES)],
  ["PluginInfo", PluginInfoSchema],
  ["ReasoningEffort", z.enum(REASONING_EFFORTS)],
  ["SettingsResponse", BackendSettingsSchema],
  ["SubagentUpdate", SubagentUpdateSchema],
  ["TransparencyDetectionResponse", TransparencyDetectionResponseSchema],
  ["WriteMode", WriteInputSchema.shape.mode],
];

describe("the frontend mirrors the backend contract", () => {
  it.each(MIRRORS)("every enum of %s matches the backend's", (name, schema) => {
    const backend = backendEnums(name);

    for (const [path, values] of frontendEnums(schema)) {
      expect(backend.get(path), `${name}${path}`).toEqual(values);
    }
  });

  it.each(contract.tools)("tool %s has an icon and a label in every language", (tool) => {
    expect(TOOL_ICONS).toHaveProperty(tool);
    expect(en.tools.names).toHaveProperty(tool);
    expect(de.tools.names).toHaveProperty(tool);
  });
});
