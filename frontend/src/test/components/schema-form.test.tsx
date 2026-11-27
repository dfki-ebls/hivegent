import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { SchemaForm } from "@/components/schema-form";

it("edits an optional parameter and one only JSON expresses, and empties an optional control to null", () => {
  const onChange = vi.fn<(values: Record<string, unknown>) => void>();
  render(
    <SchemaForm
      schema={{
        properties: {
          query: { title: "Query", anyOf: [{ type: "string" }, { type: "null" }] },
          pages: { title: "Pages", type: "array", items: { $ref: "#/$defs/Page" } },
          limit: { title: "Max Limit", anyOf: [{ type: "integer" }, { type: "null" }] },
          kind: { anyOf: [{ type: "string", enum: ["a_b"] }, { type: "null" }] },
          extra: { anyOf: [{}, { type: "null" }], default: null },
        },
        $defs: { Page: { type: "object" } },
      }}
      values={{ limit: 3, kind: null, extra: null }}
      onChange={onChange}
    />,
  );

  fireEvent.change(screen.getByLabelText("Query"), { target: { value: "tea" } });
  expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ query: "tea" }));

  fireEvent.change(screen.getByLabelText("Pages"), { target: { value: '[{"path": "a"}]' } });
  expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ pages: [{ path: "a" }] }));

  fireEvent.change(screen.getByLabelText("Max limit"), { target: { value: "" } });
  expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ limit: null }));
  expect((screen.getByLabelText("Extra") as HTMLTextAreaElement).value).toBe("");
  expect(screen.queryByDisplayValue("null")).toBeNull();
});
