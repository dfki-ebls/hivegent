/**
 * Generic JSON Schema form renderer for a tool's parameters.
 *
 * Renders each top-level property as a form field: a scalar, an enum, or a
 * list of strings gets its own control, an optional one (`anyOf` with `null`)
 * the control of its other branch, and anything else a JSON editor, so no
 * parameter is ever left out.
 */

import type { TFunction } from "i18next";
import { type ReactNode, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { OptionSelect } from "@/components/option-select";
import { cn, humanize } from "@/lib/utils";

/** A single property definition from a JSON Schema. */
interface SchemaProperty {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  items?: SchemaProperty;
  properties?: Record<string, SchemaProperty>;
  anyOf?: SchemaProperty[];
  $ref?: string;
  /** Set by `normalize` for a property that may be null, where an empty control means null. */
  nullable?: boolean;
}

/** Top-level JSON Schema structure. */
export interface JsonSchema {
  type?: string;
  title?: string;
  properties?: Record<string, SchemaProperty>;
  $defs?: Record<string, SchemaProperty>;
}

/** Whether a value is a plain JSON object rather than an array or a scalar. */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Whether a value looks like a JSON Schema whose properties the form can render. */
export function isJsonSchema(value: unknown): value is JsonSchema {
  return isRecord(value) && (!("properties" in value) || isRecord(value.properties));
}

interface SchemaFormProps {
  schema: JsonSchema;
  /**
   * A form of a record of data, such as a query: enum options are data values kept as they are,
   * a property of no type is text, a number where it reads as one, and descriptions are left out.
   */
  data?: boolean;
  className?: string;
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
}

/** Resolve $ref pointers within the schema. */
function resolveRef(
  prop: SchemaProperty,
  defs: Record<string, SchemaProperty> | undefined,
): SchemaProperty {
  const resolved = prop.$ref && defs?.[prop.$ref.replace("#/$defs/", "")];

  return resolved ? { ...resolved, ...prop, $ref: undefined } : prop;
}

/** *prop* with its references and its items' resolved, and an optional one as its one other branch. */
function normalize(
  prop: SchemaProperty,
  defs: Record<string, SchemaProperty> | undefined,
): SchemaProperty {
  const ref = resolveRef(prop, defs);
  const resolved = ref.items ? { ...ref, items: resolveRef(ref.items, defs) } : ref;
  const nullable = resolved.anyOf?.some((branch) => branch.type === "null") ?? false;
  const branches = resolved.anyOf?.filter((branch) => branch.type !== "null") ?? [];
  const [only] = branches;

  if (branches.length !== 1 || !only) return { ...resolved, nullable };

  return { ...resolveRef(only, defs), ...resolved, anyOf: undefined, nullable };
}

const scalarText = (value: unknown) =>
  typeof value === "string" || typeof value === "number" ? String(value) : "";

/** The option standing for null in an enum select, which no enum value equals. */
const NONE = "\0none";

/** *text* as a number where it reads as one, else as it is, null where empty. */
function parseUntyped(text: string): string | number | null {
  const trimmed = text.trim();

  if (trimmed === "") return null;

  return Number.isFinite(Number(trimmed)) ? Number(trimmed) : text;
}

/** A JSON editor for a value no other control fits, passing on text that does not parse yet. */
function JsonControl({
  id,
  value,
  onChange,
}: {
  id: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const [text, setText] = useState(() =>
    value === undefined || value === null ? "" : JSON.stringify(value),
  );

  return (
    <Textarea
      id={id}
      className="font-mono"
      value={text}
      onChange={(e) => {
        setText(e.target.value);

        if (e.target.value.trim() === "") {
          onChange(null);

          return;
        }

        try {
          onChange(JSON.parse(e.target.value));
        } catch {
          onChange(e.target.value);
        }
      }}
    />
  );
}

/** The control of a scalar or string list property, or `null` for a type only JSON can express. */
function scalarControl(
  id: string,
  prop: SchemaProperty,
  value: unknown,
  onChange: (value: unknown) => void,
  t: TFunction,
  { data }: Pick<SchemaFormProps, "data">,
): ReactNode {
  if (prop.type === "string" && prop.enum) {
    const options = prop.enum.map((option) => ({
      value: option,
      label: data ? option : humanize(option),
    }));
    const chosen = scalarText(value === undefined ? prop.default : value);

    return (
      <OptionSelect
        id={id}
        value={prop.nullable ? chosen || NONE : chosen || null}
        options={
          prop.nullable
            ? [{ value: NONE, label: t(($) => $.app.schemaForm.none) }, ...options]
            : options
        }
        onChange={(next) => onChange(next === NONE ? null : next)}
      />
    );
  }

  if (prop.type === "integer" || prop.type === "number") {
    return (
      <Input
        id={id}
        type="number"
        value={scalarText(value)}
        placeholder={scalarText(prop.default)}
        min={prop.minimum ?? prop.exclusiveMinimum}
        max={prop.maximum ?? prop.exclusiveMaximum}
        step={prop.type === "integer" ? 1 : undefined}
        onChange={(e) => {
          const v = e.target.value;

          if (v === "") {
            onChange(prop.nullable ? null : (prop.default ?? null));

            return;
          }

          onChange(prop.type === "integer" ? parseInt(v, 10) : parseFloat(v));
        }}
      />
    );
  }

  if (prop.type === "string") {
    return (
      <Input
        id={id}
        value={scalarText(value)}
        onChange={(e) => onChange(e.target.value === "" && prop.nullable ? null : e.target.value)}
      />
    );
  }

  if (data && !prop.type && !prop.anyOf && !prop.$ref && !prop.properties) {
    return (
      <Input
        id={id}
        value={scalarText(value)}
        onChange={(e) => onChange(parseUntyped(e.target.value))}
      />
    );
  }

  // Array of strings, edited as comma-separated values.
  if (prop.type === "array" && prop.items?.type === "string") {
    return (
      <Input
        id={id}
        value={Array.isArray(value) ? value.join(", ") : ""}
        placeholder={t(($) => $.app.schemaForm.commaSeparated)}
        onChange={(e) => {
          const items = e.target.value
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean);

          onChange(items.length === 0 && prop.nullable ? null : items);
        }}
      />
    );
  }

  return null;
}

/** Render a single form field for a property. */
function PropertyField({
  name,
  prop,
  value,
  onChange,
  data,
}: Pick<SchemaFormProps, "data"> & {
  name: string;
  prop: SchemaProperty;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const shown = value === undefined ? prop.default : value;
  const label = <FieldLabel htmlFor={id}>{humanize(prop.title || name)}</FieldLabel>;
  // Docstrings may carry a long URL, which would otherwise overflow a narrow column.
  const description = !data && prop.description && (
    <FieldDescription className="wrap-anywhere">{prop.description}</FieldDescription>
  );

  if (prop.type === "boolean") {
    return (
      <Field orientation="horizontal">
        <FieldContent>
          {label}
          {description}
        </FieldContent>
        <Switch id={id} size="sm" checked={shown === true} onCheckedChange={onChange} />
      </Field>
    );
  }

  const control = scalarControl(id, prop, shown, onChange, t, { data }) ?? (
    <JsonControl id={id} value={shown} onChange={onChange} />
  );

  return (
    <Field>
      {label}
      {control}
      {description}
    </Field>
  );
}

/** Main component: renders a JSON Schema as a form. */
export function SchemaForm({ schema, values, onChange, className, data }: SchemaFormProps) {
  const { t } = useTranslation();
  const { properties, $defs: defs } = schema;

  if (!properties || Object.keys(properties).length === 0) {
    return <FieldDescription>{t(($) => $.app.schemaForm.noOptions)}</FieldDescription>;
  }

  return (
    <FieldGroup className={cn("gap-4", className)}>
      {Object.entries(properties).map(([key, prop]) => (
        <PropertyField
          key={key}
          name={key}
          prop={normalize(prop, defs)}
          value={values[key]}
          onChange={(v) => onChange({ ...values, [key]: v })}
          data={data}
        />
      ))}
    </FieldGroup>
  );
}
