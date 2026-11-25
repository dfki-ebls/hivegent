import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SchemaForm } from "@/components/schema-form";

describe("SchemaForm", () => {
  it("renders an optional parameter and one only JSON expresses", () => {
    const onChange = vi.fn<(values: Record<string, unknown>) => void>();
    render(
      <SchemaForm
        schema={{
          properties: {
            query: { title: "Query", anyOf: [{ type: "string" }, { type: "null" }] },
            pages: { title: "Pages", type: "array", items: { $ref: "#/$defs/Page" } },
          },
          $defs: { Page: { type: "object" } },
        }}
        values={{}}
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByLabelText("Query"), { target: { value: "tea" } });
    expect(onChange).toHaveBeenLastCalledWith({ query: "tea" });

    fireEvent.change(screen.getByLabelText("Pages"), { target: { value: '[{"path": "a"}]' } });
    expect(onChange).toHaveBeenLastCalledWith({ pages: [{ path: "a" }] });
  });
});
