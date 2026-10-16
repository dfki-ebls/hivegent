/**
 * Shared dialog for configuring conversion and chunking pipeline options.
 *
 * Renders form fields generically from the pipeline's JSON Schema,
 * with an advanced JSON editor for power users.
 */

import { RotateCcwIcon, SettingsIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import type { PipelineKind } from "@/lib/types";
import { TooltipButton } from "@/components/TooltipButton";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Field, FieldError } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import { isJsonSchema, isRecord, SchemaForm } from "@/components/SchemaForm";

interface PipelineConfigDialogProps {
  /** Pipeline display label (e.g. "Docling", "Token"). */
  pipelineLabel: string;
  /** Whether this is a "conversion" or "chunking" pipeline. */
  pipelineType: PipelineKind;
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
  disabled,
  ...form
}: PipelineConfigDialogProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <TooltipButton
            label={t(($) => $.documents.pipelineConfig.configure, { pipeline: pipelineLabel })}
            className={cn(Object.keys(form.currentConfig).length > 0 && "text-primary")}
            disabled={disabled}
          />
        }
      >
        <SettingsIcon />
      </DialogTrigger>
      <DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {t(($) => $.documents.pipelineConfig.title[pipelineType], { pipeline: pipelineLabel })}
          </DialogTitle>
          <DialogDescription>
            {t(($) => $.documents.pipelineConfig.description, { pipeline: pipelineLabel })}
          </DialogDescription>
        </DialogHeader>
        <PipelineConfigForm {...form} onDone={() => setOpen(false)} />
      </DialogContent>
    </Dialog>
  );
}

// Mounts with the dialog content, so every opening loads the current config.
function PipelineConfigForm({
  configSchema,
  configDefaults,
  currentConfig,
  onSave,
  onReset,
  onDone,
}: Omit<PipelineConfigDialogProps, "pipelineLabel" | "pipelineType" | "disabled"> & {
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [values, setValues] = useState(() => ({ ...configDefaults, ...currentConfig }));
  const [advancedJson, setAdvancedJson] = useState(() => JSON.stringify(values, null, 2));
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const schema = isJsonSchema(configSchema) ? configSchema : {};
  const hasSchema = Object.keys(schema.properties ?? {}).length > 0;

  // Form edits rewrite the JSON, while JSON edits update the form once they parse.
  const handleFormChange = (next: Record<string, unknown>) => {
    setValues(next);
    setAdvancedJson(JSON.stringify(next, null, 2));
    setJsonError(null);
  };

  const handleAdvancedChange = (json: string) => {
    setAdvancedJson(json);

    try {
      const parsed: unknown = JSON.parse(json);

      if (!isRecord(parsed)) throw new TypeError("not an object");

      setValues(parsed);
      setJsonError(null);
    } catch {
      setJsonError(t(($) => $.documents.pipelineConfig.invalidJson));
    }
  };

  // Only fields that differ from the defaults are saved.
  const handleSave = () => {
    onSave(
      Object.fromEntries(
        Object.entries(values).filter(
          ([key, value]) => JSON.stringify(value) !== JSON.stringify(configDefaults[key]),
        ),
      ),
    );
    onDone();
  };

  return (
    <>
      <div className="flex flex-col gap-4">
        <SchemaForm schema={schema} values={values} onChange={handleFormChange} />
        {hasSchema && (
          <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
            <CollapsibleTrigger render={<Button variant="ghost" size="sm" />}>
              {advancedOpen
                ? t(($) => $.documents.pipelineConfig.hideAdvanced)
                : t(($) => $.documents.pipelineConfig.showAdvanced)}
            </CollapsibleTrigger>
            <CollapsibleContent>
              <Field data-invalid={jsonError !== null} className="mt-2">
                <Textarea
                  value={advancedJson}
                  aria-invalid={jsonError !== null}
                  onChange={(e) => handleAdvancedChange(e.target.value)}
                  className="min-h-30 font-mono text-xs"
                />
                <FieldError>{jsonError}</FieldError>
              </Field>
            </CollapsibleContent>
          </Collapsible>
        )}
      </div>
      <DialogFooter>
        <Button
          variant="outline"
          onClick={() => {
            onReset();
            onDone();
          }}
        >
          <RotateCcwIcon data-icon="inline-start" />
          {t(($) => $.documents.pipelineConfig.resetDefaults)}
        </Button>
        <Button onClick={handleSave} disabled={jsonError !== null}>
          {t(($) => $.common.actions.save)}
        </Button>
      </DialogFooter>
    </>
  );
}
