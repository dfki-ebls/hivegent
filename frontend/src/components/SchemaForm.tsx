/**
 * Generic JSON Schema form renderer.
 *
 * Renders top-level scalar properties from a JSON Schema as form fields.
 * Nested objects are rendered as collapsible sections with their own fields.
 * Deeply nested or unsupported types are skipped (handled by the Advanced JSON editor).
 */

import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Switch } from "./ui/switch";

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
  if (prop.$ref && defs) {
    const refName = prop.$ref.replace("#/$defs/", "");
    const resolved = defs[refName];
    if (resolved) return { ...resolved, ...prop, $ref: undefined };
  }
  return prop;
}

/** Check if a schema property has renderable scalar fields. */
function hasProperties(schema: JsonSchema | SchemaProperty): boolean {
  return !!schema.properties && Object.keys(schema.properties).length > 0;
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
  const label = prop.title || name;
  const id = `schema-field-${name}`;

  // Boolean → Switch
  if (prop.type === "boolean") {
    return (
      <div className="flex items-center justify-between gap-4">
        <div className="grid gap-0.5">
          <Label htmlFor={id}>{label}</Label>
          {prop.description && <p className="text-xs text-muted-foreground">{prop.description}</p>}
        </div>
        <Switch
          id={id}
          size="sm"
          checked={value === true}
          onCheckedChange={(checked) => onChange(checked)}
        />
      </div>
    );
  }

  // String with enum → Select
  if (prop.type === "string" && prop.enum) {
    return (
      <div className="grid gap-1.5">
        <Label htmlFor={id}>{label}</Label>
        <Select value={String(value ?? prop.default ?? "")} onValueChange={(v) => onChange(v)}>
          <SelectTrigger id={id}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {prop.enum.map((opt) => (
              <SelectItem key={opt} value={opt}>
                {opt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {prop.description && <p className="text-xs text-muted-foreground">{prop.description}</p>}
      </div>
    );
  }

  // Integer / Number → Input type=number
  if (prop.type === "integer" || prop.type === "number") {
    return (
      <div className="grid gap-1.5">
        <Label htmlFor={id}>{label}</Label>
        <Input
          id={id}
          type="number"
          value={value !== undefined && value !== null ? String(value) : ""}
          min={prop.minimum ?? prop.exclusiveMinimum}
          max={prop.maximum ?? prop.exclusiveMaximum}
          step={prop.type === "integer" ? 1 : undefined}
          onChange={(e) => {
            const v = e.target.value;
            if (v === "") {
              onChange(prop.default ?? 0);
            } else {
              onChange(prop.type === "integer" ? parseInt(v, 10) : parseFloat(v));
            }
          }}
        />
        {prop.description && <p className="text-xs text-muted-foreground">{prop.description}</p>}
      </div>
    );
  }

  // String → Input
  if (prop.type === "string") {
    return (
      <div className="grid gap-1.5">
        <Label htmlFor={id}>{label}</Label>
        <Input id={id} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />
        {prop.description && <p className="text-xs text-muted-foreground">{prop.description}</p>}
      </div>
    );
  }

  // Array of strings → comma-separated input
  if (prop.type === "array" && prop.items?.type === "string") {
    const arr = Array.isArray(value) ? value : [];
    return (
      <div className="grid gap-1.5">
        <Label htmlFor={id}>{label}</Label>
        <Input
          id={id}
          value={arr.join(", ")}
          placeholder="Comma-separated values"
          onChange={(e) => {
            const v = e.target.value;
            onChange(
              v
                ? v
                    .split(",")
                    .map((s: string) => s.trim())
                    .filter(Boolean)
                : [],
            );
          }}
        />
        {prop.description && <p className="text-xs text-muted-foreground">{prop.description}</p>}
      </div>
    );
  }

  return null;
}

/** Render form fields for an object's properties. */
function ObjectFields({
  properties,
  defs,
  values,
  onChange,
  prefix,
}: {
  properties: Record<string, SchemaProperty>;
  defs?: Record<string, SchemaProperty>;
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
  prefix?: string;
}) {
  return (
    <div className="grid gap-4">
      {Object.entries(properties).map(([key, rawProp]) => {
        const prop = resolveRef(rawProp, defs);
        const fieldKey = prefix ? `${prefix}.${key}` : key;

        // Skip deeply nested objects (handled by Advanced JSON)
        if (prop.type === "object" || (prop.properties && !prop.type)) {
          return null;
        }

        return (
          <ScalarField
            key={fieldKey}
            name={key}
            prop={prop}
            value={values[key]}
            onChange={(v) => onChange({ ...values, [key]: v })}
          />
        );
      })}
    </div>
  );
}

/** Main component: renders a JSON Schema as a form. */
export function SchemaForm({ schema, values, onChange }: SchemaFormProps) {
  if (!schema.properties || !hasProperties(schema)) {
    return (
      <p className="text-sm text-muted-foreground">
        No configuration options available for this pipeline.
      </p>
    );
  }

  const defs = schema.$defs;
  const topLevelScalars: Record<string, SchemaProperty> = {};
  const nestedObjects: Array<{
    key: string;
    prop: SchemaProperty;
  }> = [];

  for (const [key, rawProp] of Object.entries(schema.properties)) {
    const prop = resolveRef(rawProp, defs);
    if (prop.type === "object" || (prop.properties && !prop.type)) {
      nestedObjects.push({ key, prop });
    } else {
      topLevelScalars[key] = prop;
    }
  }

  return (
    <div className="grid gap-6">
      {/* Top-level scalar fields */}
      {Object.keys(topLevelScalars).length > 0 && (
        <ObjectFields
          properties={topLevelScalars}
          defs={defs}
          values={values}
          onChange={onChange}
        />
      )}

      {/* Nested object sections */}
      {nestedObjects.map(({ key, prop }) => {
        if (!prop.properties || !hasProperties(prop)) return null;
        const nestedValues = (values[key] as Record<string, unknown>) ?? {};
        return (
          <div key={key} className="grid gap-3">
            <h4 className="text-sm font-medium">{prop.title || key}</h4>
            {prop.description && (
              <p className="text-xs text-muted-foreground -mt-2">{prop.description}</p>
            )}
            <ObjectFields
              properties={prop.properties}
              defs={defs}
              values={nestedValues}
              onChange={(nested) => onChange({ ...values, [key]: nested })}
              prefix={key}
            />
          </div>
        );
      })}
    </div>
  );
}
