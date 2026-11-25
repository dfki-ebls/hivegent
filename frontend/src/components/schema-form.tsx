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
  anyOf?: SchemaProperty[];
  $ref?: string;
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
  const branches = resolved.anyOf?.filter((branch) => branch.type !== "null") ?? [];
  const [only] = branches;

  if (branches.length !== 1 || !only) return resolved;

  return { ...resolveRef(only, defs), ...resolved, anyOf: undefined };
}

const scalarText = (value: unknown) =>
  typeof value === "string" || typeof value === "number" ? String(value) : "";

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
  const [text, setText] = useState(() => (value === undefined ? "" : JSON.stringify(value)));

  return (
    <Textarea
      id={id}
      className="font-mono"
      value={text}
      onChange={(e) => {
        setText(e.target.value);

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
): ReactNode {
  if (prop.type === "string" && prop.enum) {
    return (
      <OptionSelect
        id={id}
        value={scalarText(value ?? prop.default)}
        options={prop.enum.map((option) => ({ value: option, label: option }))}
        onChange={onChange}
      />
    );
  }

  if (prop.type === "integer" || prop.type === "number") {
    return (
      <Input
        id={id}
        type="number"
        value={scalarText(value)}
        min={prop.minimum ?? prop.exclusiveMinimum}
        max={prop.maximum ?? prop.exclusiveMaximum}
        step={prop.type === "integer" ? 1 : undefined}
        onChange={(e) => {
          const v = e.target.value;
          onChange(
            v === ""
              ? (prop.default ?? 0)
              : prop.type === "integer"
                ? parseInt(v, 10)
                : parseFloat(v),
          );
        }}
      />
    );
  }

  if (prop.type === "string") {
    return <Input id={id} value={scalarText(value)} onChange={(e) => onChange(e.target.value)} />;
  }

  // Array of strings, edited as comma-separated values.
  if (prop.type === "array" && prop.items?.type === "string") {
    return (
      <Input
        id={id}
        value={Array.isArray(value) ? value.join(", ") : ""}
        placeholder={t(($) => $.app.schemaForm.commaSeparated)}
        onChange={(e) =>
          onChange(
            e.target.value
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean),
          )
        }
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
}: {
  name: string;
  prop: SchemaProperty;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const label = <FieldLabel htmlFor={id}>{prop.title || name}</FieldLabel>;
  const description = prop.description && <FieldDescription>{prop.description}</FieldDescription>;

  if (prop.type === "boolean") {
    return (
      <Field orientation="horizontal">
        <FieldContent>
          {label}
          {description}
        </FieldContent>
        <Switch id={id} size="sm" checked={value === true} onCheckedChange={onChange} />
      </Field>
    );
  }

  const control = scalarControl(id, prop, value, onChange, t) ?? (
    <JsonControl id={id} value={value ?? prop.default} onChange={onChange} />
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
export function SchemaForm({ schema, values, onChange }: SchemaFormProps) {
  const { t } = useTranslation();
  const { properties, $defs: defs } = schema;

  if (!properties || Object.keys(properties).length === 0) {
    return <FieldDescription>{t(($) => $.app.schemaForm.noOptions)}</FieldDescription>;
  }

  return (
    <FieldGroup className="gap-4">
      {Object.entries(properties).map(([key, prop]) => (
        <PropertyField
          key={key}
          name={key}
          prop={normalize(prop, defs)}
          value={values[key]}
          onChange={(v) => onChange({ ...values, [key]: v })}
        />
      ))}
    </FieldGroup>
  );
}
