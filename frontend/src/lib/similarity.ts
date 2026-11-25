/**
 * The similarity configuration of a case base (a cbrkit specification) as plain JSON,
 * and the Mermaid diagram of a graph match, without React.
 */
import { z } from "zod";

import { formatScore } from "@/i18n/format";
import { humanize } from "@/lib/utils";

const JsonValueSchema = z.json();
export type Json = z.infer<typeof JsonValueSchema>;
export type JsonObject = { [key: string]: Json };

export function isJsonObject(value: Json | undefined): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** *values* as a JSON object, null while a field holds text that is no JSON yet. */
export function jsonObject(values: Record<string, unknown>): JsonObject | null {
  const parsed = JsonValueSchema.safeParse(values).data;

  return isJsonObject(parsed) ? parsed : null;
}

function segments(pointer: string): string[] {
  return pointer
    .split("/")
    .slice(1)
    .map((segment) => segment.replaceAll("~1", "/").replaceAll("~0", "~"));
}

/** The label of an element's pointer, its attribute names humanized and joined by `›`, empty for the whole. */
export function elementLabel(pointer: string): string {
  return segments(pointer)
    .filter((_, index) => index % 2 === 1)
    .map(humanize)
    .join(" › ");
}

/** The value at a JSON pointer, undefined where it names nothing. */
export function valueAt(tree: Json, pointer: string): Json | undefined {
  let node: Json | undefined = tree;

  for (const segment of segments(pointer)) {
    node = isJsonObject(node) ? node[segment] : undefined;
  }

  return node;
}

/** The pointer of the attribute-value measure holding the attribute at *pointer*, and its name. */
function attributeOf(pointer: string): { container: string; name: string } | null {
  const cut = pointer.lastIndexOf("/");
  const head = pointer.slice(0, cut);

  if (!head.endsWith("/attributes")) return null;

  const [name = ""] = segments(pointer.slice(cut));

  return { container: head.slice(0, -"/attributes".length), name };
}

/** The weight of the attribute at *pointer*, null for a measure that is no attribute. */
export function weightAt(measure: Json, pointer: string): number | null {
  const attribute = attributeOf(pointer);

  if (!attribute) return null;

  const aggregator = valueAt(measure, `${attribute.container}/aggregator`);
  const weights = isJsonObject(aggregator) ? aggregator.weights : undefined;
  const fallback = isJsonObject(aggregator) ? aggregator.default_weight : undefined;
  const weight = isJsonObject(weights) ? weights[attribute.name] : undefined;

  return typeof weight === "number" ? weight : typeof fallback === "number" ? fallback : 1;
}

/** A graph in the serialized form of cbrkit. */
const GraphSchema = z.object({
  nodes: z.record(z.string(), z.json()),
  edges: z.record(z.string(), z.object({ source: z.json(), target: z.json() })).default({}),
});

/** A key or value as text, JSON for anything but a string or a number, empty for none. */
export function jsonText(value: Json | undefined): string {
  if (value === null || value === undefined) return "";

  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : JSON.stringify(value);
}

function label(key: string, value: Json | undefined): string {
  const text = isJsonObject(value) ? (value.name ?? value.type ?? key) : (value ?? key);

  return jsonText(text).replaceAll('"', "#quot;");
}

/**
 * A Mermaid flowchart of a case graph, its nodes a query node is mapped onto
 * highlighted and labelled with that node and their similarity.
 */
export function mappingDiagram(
  caseGraph: Json,
  queryGraph: Json,
  mapping: readonly { query: Json; case: Json; similarity: number }[],
): string | null {
  const graph = GraphSchema.safeParse(caseGraph).data;
  const query = GraphSchema.safeParse(queryGraph).data;

  if (!graph) return null;

  const ids = new Map(Object.keys(graph.nodes).map((key, index) => [key, `n${index}`]));
  const mapped = new Map(mapping.map((item) => [jsonText(item.case), item]));
  const lines = ["flowchart LR"];

  for (const [key, value] of Object.entries(graph.nodes)) {
    const item = mapped.get(key);
    const from = item
      ? ` ← ${label(jsonText(item.query), query?.nodes[jsonText(item.query)])} ${formatScore(item.similarity)}`
      : "";
    lines.push(`  ${ids.get(key)}["${label(key, value)}${from}"]${item ? ":::mapped" : ""}`);
  }

  for (const edge of Object.values(graph.edges)) {
    const source = ids.get(jsonText(edge.source));
    const target = ids.get(jsonText(edge.target));

    if (source && target) lines.push(`  ${source} --> ${target}`);
  }

  lines.push("  classDef mapped stroke-width:3px");

  return lines.join("\n");
}
