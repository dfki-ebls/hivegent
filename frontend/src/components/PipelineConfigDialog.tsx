/**
 * Shared dialog for configuring conversion and chunking pipeline options.
 *
 * Renders form fields generically from the pipeline's JSON Schema,
 * with an advanced JSON editor for power users.
 */

import { RotateCcwIcon, SettingsIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "./ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "./ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "./ui/dialog";
import { Textarea } from "./ui/textarea";
import { type JsonSchema, SchemaForm } from "./SchemaForm";

/** Type guard: check that a value looks like a JSON Schema with properties. */
function isJsonSchema(v: unknown): v is JsonSchema {
  return (
    typeof v === "object" &&
    v !== null &&
    (!("properties" in v) ||
      typeof (v as Record<string, unknown>).properties === "object")
  );
}

interface PipelineConfigDialogProps {
  /** Pipeline display label (e.g. "Docling", "Token"). */
  pipelineLabel: string;
  /** Whether this is a "conversion" or "chunking" pipeline. */
  pipelineType: "conversion" | "chunking";
  /** The pipeline's JSON Schema for its config model. */
  configSchema: Record<string, unknown>;
  /** The pipeline's default config values. */
  configDefaults: Record<string, unknown>;
  /** Current user-configured values (may be empty if using defaults). */
  currentConfig: Record<string, unknown>;
  /** Called when the user saves a new configuration. */
  onSave: (config: Record<string, unknown>) => void;
  /** Called when the user resets to defaults. */
  onReset: () => void;
  /** Whether the trigger button should be disabled. */
  disabled?: boolean;
}

export function PipelineConfigDialog({
  pipelineLabel,
  pipelineType,
  configSchema,
  configDefaults,
  currentConfig,
  onSave,
  onReset,
  disabled,
}: PipelineConfigDialogProps) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [advancedJson, setAdvancedJson] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  // Sync local state when dialog opens
  useEffect(() => {
    if (open) {
      const merged = { ...configDefaults, ...currentConfig };
      setValues(merged);
      setAdvancedJson(JSON.stringify(merged, null, 2));
      setJsonError(null);
      setAdvancedOpen(false);
    }
  }, [open, configDefaults, currentConfig]);

  // Sync advanced JSON when form values change (and advanced is not focused)
  const syncJsonFromValues = (newValues: Record<string, unknown>) => {
    setValues(newValues);
    setAdvancedJson(JSON.stringify(newValues, null, 2));
    setJsonError(null);
  };

  const handleAdvancedChange = (json: string) => {
    setAdvancedJson(json);
    try {
      const parsed = JSON.parse(json) as Record<string, unknown>;
      setValues(parsed);
      setJsonError(null);
    } catch {
      setJsonError("Invalid JSON");
    }
  };

  const handleSave = () => {
    // Only save fields that differ from defaults
    const diff: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(values)) {
      if (JSON.stringify(val) !== JSON.stringify(configDefaults[key])) {
        diff[key] = val;
      }
    }
    onSave(diff);
    setOpen(false);
  };

  const handleReset = () => {
    setValues({ ...configDefaults });
    setAdvancedJson(JSON.stringify(configDefaults, null, 2));
    setJsonError(null);
    onReset();
    setOpen(false);
  };

  const hasConfig = Object.keys(currentConfig).length > 0;
  const validSchema = isJsonSchema(configSchema) ? configSchema : undefined;
  const hasSchema = Object.keys(validSchema?.properties ?? {}).length > 0;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 shrink-0"
          disabled={disabled}
          title={`Configure ${pipelineLabel}`}
        >
          <SettingsIcon
            className={`h-3.5 w-3.5 ${hasConfig ? "text-primary" : ""}`}
          />
          <span className="sr-only">Configure {pipelineLabel}</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {pipelineLabel}{" "}
            {pipelineType === "conversion" ? "Conversion" : "Chunking"} Settings
          </DialogTitle>
          <DialogDescription>
            Configure options for the {pipelineLabel} pipeline.
          </DialogDescription>
        </DialogHeader>

        <div className="py-4 grid gap-4">
          {hasSchema ? (
            <SchemaForm
              schema={validSchema!}
              values={values}
              onChange={syncJsonFromValues}
            />
          ) : (
            <p className="text-sm text-muted-foreground">
              No configuration options available for this pipeline.
            </p>
          )}

          {/* Advanced JSON editor */}
          {hasSchema && (
            <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
              <CollapsibleTrigger asChild>
                <Button variant="ghost" size="sm" className="w-full justify-start text-xs">
                  {advancedOpen ? "Hide" : "Show"} Advanced JSON
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <div className="grid gap-1.5 mt-2">
                  <Textarea
                    value={advancedJson}
                    onChange={(e) => handleAdvancedChange(e.target.value)}
                    className="font-mono text-xs min-h-[120px] resize-y"
                  />
                  {jsonError && (
                    <p className="text-xs text-destructive">{jsonError}</p>
                  )}
                </div>
              </CollapsibleContent>
            </Collapsible>
          )}
        </div>

        <DialogFooter className="gap-2 sm:gap-0">
          <Button
            variant="outline"
            size="sm"
            onClick={handleReset}
          >
            <RotateCcwIcon className="h-3.5 w-3.5 mr-1.5" />
            Reset to Defaults
          </Button>
          <Button
            size="sm"
            onClick={handleSave}
            disabled={!!jsonError}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
