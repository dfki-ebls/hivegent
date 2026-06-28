import { FileType } from "lucide-react";
import { useEffect, useState } from "react";

import { listConversionPipelines } from "@/lib/api";
import {
  ConversionPipeline,
  type ConversionPipelineInfo,
  ConversionPipelineSchema,
} from "@/lib/types";
import { useSettingsStore } from "@/stores/settings-store";
import { PipelineConfigDialog } from "@/components/PipelineConfigDialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface ConversionPipelineSelectorProps {
  value: ConversionPipeline;
  onChange: (value: ConversionPipeline) => void;
  disabled?: boolean;
}

export function ConversionPipelineSelector({
  value,
  onChange,
  disabled,
}: ConversionPipelineSelectorProps) {
  const [pipelines, setPipelines] = useState<ConversionPipelineInfo[]>([]);

  const conversionConfigs = useSettingsStore((s) => s.conversionConfigs);
  const setConversionConfig = useSettingsStore((s) => s.setConversionConfig);
  const resetConversionConfig = useSettingsStore((s) => s.resetConversionConfig);

  useEffect(() => {
    listConversionPipelines()
      .then(setPipelines)
      .catch(() => {
        // Silently fail — selector will be empty until pipelines load
      });
  }, []);

  const selectedPipeline = pipelines.find((p) => p.value === value);
  const isAuto = value === ConversionPipeline.AUTO;

  return (
    <div className="flex items-center gap-2">
      <Label
        htmlFor="conversion-pipeline-select"
        className="text-sm text-muted-foreground flex items-center gap-1.5"
      >
        <FileType className="h-4 w-4" />
        Conversion
      </Label>
      <Select
        value={value}
        onValueChange={(v) => {
          const parsed = ConversionPipelineSchema.safeParse(v);
          if (parsed.success) onChange(parsed.data);
        }}
        disabled={disabled}
      >
        <SelectTrigger id="conversion-pipeline-select" className="w-[140px]" size="sm">
          <SelectValue placeholder="Select conversion" />
        </SelectTrigger>
        <SelectContent>
          {pipelines.map((p) => (
            <SelectItem key={p.value} value={p.value}>
              {p.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {selectedPipeline && !isAuto && (
        <PipelineConfigDialog
          pipelineLabel={selectedPipeline.label}
          pipelineType="conversion"
          configSchema={selectedPipeline.config_schema ?? {}}
          configDefaults={selectedPipeline.config_defaults ?? {}}
          currentConfig={conversionConfigs[value] ?? {}}
          onSave={(config) => setConversionConfig(value, config)}
          onReset={() => resetConversionConfig(value)}
          disabled={disabled}
        />
      )}
    </div>
  );
}
