import { FileType, type LucideIcon, Scissors } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { z } from "zod";

import {
  getChunkingPipelineConfig,
  getConversionPipelineConfig,
  listChunkingPipelines,
  listConversionPipelines,
} from "@/lib/api";
import {
  ChunkingPipeline,
  ChunkingPipelineSchema,
  ConversionPipeline,
  ConversionPipelineSchema,
  type PipelineConfigInfo,
  type PipelineKind,
} from "@/lib/types";
import { keyPrefix } from "@/i18n";
import { usePipelineConfig } from "@/hooks/use-pipeline-config";
import { PIPELINE_CONFIG_FIELDS, useSettingsStore } from "@/stores/settings-store";
import { PipelineConfigDialog } from "@/components/PipelineConfigDialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const T_OPTIONS = keyPrefix(($) => $.documents.pipelines);

interface PipelineValues {
  conversion: ConversionPipeline;
  chunking: ChunkingPipeline;
}

interface PipelineRegistry<P extends string> {
  icon: LucideIcon;
  schema: z.ZodType<P>;
  auto: P;
  list: () => Promise<{ value: string; label: string }[]>;
  fetchConfig: (pipeline: P) => Promise<PipelineConfigInfo>;
}

const REGISTRIES: { [K in PipelineKind]: PipelineRegistry<PipelineValues[K]> } = {
  conversion: {
    icon: FileType,
    schema: ConversionPipelineSchema,
    auto: ConversionPipeline.AUTO,
    list: listConversionPipelines,
    fetchConfig: getConversionPipelineConfig,
  },
  chunking: {
    icon: Scissors,
    schema: ChunkingPipelineSchema,
    auto: ChunkingPipeline.AUTO,
    list: listChunkingPipelines,
    fetchConfig: getChunkingPipelineConfig,
  },
};

interface PipelineSelectorProps<K extends PipelineKind> {
  kind: K;
  value: PipelineValues[K];
  onChange: (value: PipelineValues[K]) => void;
  disabled?: boolean;
}

/** Pick the conversion or chunking pipeline, with a dialog for the selected one's configuration. */
export function PipelineSelector<K extends PipelineKind>({
  kind,
  value,
  onChange,
  disabled,
}: PipelineSelectorProps<K>) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const registry: PipelineRegistry<PipelineValues[K]> = REGISTRIES[kind];
  const [pipelines, setPipelines] = useState<{ value: string; label: string }[]>([]);

  const configs = useSettingsStore((s) => s[PIPELINE_CONFIG_FIELDS[kind]]);
  const setPipelineConfig = useSettingsStore((s) => s.setPipelineConfig);
  const resetPipelineConfig = useSettingsStore((s) => s.resetPipelineConfig);

  useEffect(() => {
    registry
      .list()
      .then(setPipelines)
      .catch(() => {
        // Silently fail, the selector stays empty until the pipelines load.
      });
  }, [registry]);

  const selectedPipeline = pipelines.find((p) => p.value === value);
  const pipelineConfig = usePipelineConfig(
    value === registry.auto ? null : value,
    registry.fetchConfig,
  );
  const Icon = registry.icon;
  const id = `${kind}-pipeline-select`;
  // Widened from `K`, since the selector API cannot index a catalog by a type parameter.
  const stage: PipelineKind = kind;

  return (
    <div className="flex items-center gap-2">
      <Label htmlFor={id} className="text-sm text-muted-foreground flex items-center gap-1.5">
        <Icon className="h-4 w-4" />
        {t(($) => $[stage])}
      </Label>
      <Select
        value={value}
        onValueChange={(v) => {
          const parsed = registry.schema.safeParse(v);
          if (parsed.success) onChange(parsed.data);
        }}
        disabled={disabled}
      >
        <SelectTrigger id={id} className="w-[140px]" size="sm">
          <SelectValue placeholder={t(($) => $.select[stage])} />
        </SelectTrigger>
        <SelectContent>
          {pipelines.map((pipeline) => (
            <SelectItem key={pipeline.value} value={pipeline.value}>
              {pipeline.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {selectedPipeline && pipelineConfig && (
        <PipelineConfigDialog
          pipelineLabel={selectedPipeline.label}
          pipelineType={kind}
          configSchema={pipelineConfig.schema}
          configDefaults={pipelineConfig.defaults}
          currentConfig={configs[value] ?? {}}
          onSave={(config) => setPipelineConfig(kind, value, config)}
          onReset={() => resetPipelineConfig(kind, value)}
          disabled={disabled}
        />
      )}
    </div>
  );
}
