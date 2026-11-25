import { describe, expect, it } from "vitest";

import { humanize } from "@/lib/utils";
import { elementLabel, jsonText, mappingDiagram, weightAt } from "@/lib/similarity";

const measure = {
  kind: "attribute_value",
  attributes: { price: { kind: "linear", max_distance: 10 }, "a/b": { kind: "equality" } },
  aggregator: { weights: { price: 2 }, default_weight: 1 },
};

describe("similarity", () => {
  it("reads the weights of attributes by JSON pointer", () => {
    expect(weightAt(measure, "/attributes/price")).toBe(2);
    expect(weightAt(measure, "/attributes/a~1b")).toBe(1);
    expect(weightAt(measure, "")).toBeNull();
  });

  it("draws the case graph with the mapped nodes highlighted", () => {
    const diagram = mappingDiagram(
      {
        nodes: { s: { name: "Start" }, t: { name: "Ship" } },
        edges: { e: { source: "s", target: "t" } },
      },
      { nodes: { q: { name: "Send" } }, edges: {} },
      [{ query: "q", case: "t", similarity: 0.5 }],
    );

    expect(diagram).toContain('n1["Ship ← Send 0.50"]:::mapped');
    expect(diagram).toContain("n0 --> n1");
  });

  it("labels identifiers and leaves none empty", () => {
    expect(
      ["title_status", "numeric-linear", "maxLimit", "Max Limit", "URL"].map(humanize),
    ).toEqual(["Title status", "Numeric linear", "Max limit", "Max limit", "URL"]);
    expect(elementLabel("/attributes/a_b/attributes/c")).toBe("A b › C");
    expect(elementLabel("")).toBe("");
    expect([null, undefined, "x", 2].map(jsonText)).toEqual(["", "", "x", "2"]);
  });
});
