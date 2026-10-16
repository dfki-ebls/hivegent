/**
 * Generic JSON Schema form renderer.
 *
 * Renders top-level scalar properties from a JSON Schema as form fields.
 * Nested objects are rendered as field sets with their own scalar fields.
 * Deeply nested or unsupported types are skipped (handled by the Advanced JSON editor).
 */

import type { TFunction } from "i18next";
import { type ReactNode, useId } from "react";
import { useTranslation } from "react-i18next";

import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { OptionSelect } from "@/components/OptionSelect";

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
  properties?: Record<string, SchemaProperty>;
  items?: SchemaProperty;
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
export function isRecord(value: unknown): value is Record<string, unknown> {
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

function isObjectProperty(prop: SchemaProperty): boolean {
  return prop.type === "object" || (prop.properties !== undefined && !prop.type);
}

const scalarText = (value: unknown) =>
  typeof value === "string" || typeof value === "number" ? String(value) : "";

/** The control of a scalar property, or `null` for an unsupported type. */
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
          onChange(v === "" ? (prop.default ?? 0) : prop.type === "integer" ? parseInt(v, 10) : parseFloat(v));
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

/** Render a single form field for a scalar property. */
function ScalarField({
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

  const control = scalarControl(id, prop, value, onChange, t);

  if (!control) return null;

  return (
    <Field>
      {label}
      {control}
      {description}
    </Field>
  );
}

/** Render form fields for an object's scalar properties, skipping nested objects. */
function ObjectFields({
  properties,
  defs,
  values,
  onChange,
}: {
  properties: Record<string, SchemaProperty>;
  defs?: Record<string, SchemaProperty>;
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
}) {
  return (
    <FieldGroup className="gap-4">
      {Object.entries(properties).map(([key, rawProp]) => {
        const prop = resolveRef(rawProp, defs);

        if (isObjectProperty(prop)) return null;

        return (
          <ScalarField
            key={key}
            name={key}
            prop={prop}
            value={values[key]}
            onChange={(v) => onChange({ ...values, [key]: v })}
          />
        );
      })}
    </FieldGroup>
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
    <FieldGroup className="gap-6">
      <ObjectFields properties={properties} defs={defs} values={values} onChange={onChange} />
      {Object.entries(properties).map(([key, rawProp]) => {
        const prop = resolveRef(rawProp, defs);

        if (!isObjectProperty(prop) || !prop.properties) return null;

        const nested = values[key];

        return (
          <FieldSet key={key} className="gap-3">
            <FieldLegend variant="label" className="mb-0">
              {prop.title || key}
            </FieldLegend>
            {prop.description && <FieldDescription>{prop.description}</FieldDescription>}
            <ObjectFields
              properties={prop.properties}
              defs={defs}
              values={isRecord(nested) ? nested : {}}
              onChange={(next) => onChange({ ...values, [key]: next })}
            />
          </FieldSet>
        );
      })}
    </FieldGroup>
  );
}
