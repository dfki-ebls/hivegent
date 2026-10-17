import type { TFunction } from "i18next";
import { FileType, type LucideIcon, Scissors } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  ChunkingPipeline,
  ConversionPipeline,
  type PipelineByKind,
  type PipelineKind,
} from "@/lib/types";
import { usePipelineConfig, usePipelines } from "@/hooks/use-pipeline-config";
import { PIPELINE_CONFIG_FIELDS, useSettingsStore } from "@/stores/settings-store";
import { PipelineConfigDialog } from "@/components/PipelineConfigDialog";
import { LabeledOptionSelect } from "@/components/OptionSelect";

interface PipelineRegistry<P extends string> {
  icon: LucideIcon;
  auto: P;
  text: (t: TFunction, pipeline: P) => { label: string; description: string };
}

const REGISTRIES: { [K in PipelineKind]: PipelineRegistry<PipelineByKind[K]> } = {
  conversion: {
    icon: FileType,
    auto: ConversionPipeline.AUTO,
    text: (t, pipeline) =>
      t(($) => $.options.conversionPipeline[pipeline], { returnObjects: true }),
  },
  chunking: {
    icon: Scissors,
    auto: ChunkingPipeline.AUTO,
    text: (t, pipeline) => t(($) => $.options.chunkingPipeline[pipeline], { returnObjects: true }),
  },
};

interface PipelineSelectorProps<K extends PipelineKind> {
  kind: K;
  value: PipelineByKind[K];
  onChange: (value: PipelineByKind[K]) => void;
  disabled?: boolean;
}

/** Pick the conversion or chunking pipeline, with a dialog for the selected one's configuration. */
export function PipelineSelector<K extends PipelineKind>({
  kind,
  value,
  onChange,
  disabled,
}: PipelineSelectorProps<K>) {
  const { t } = useTranslation();
  const registry: PipelineRegistry<PipelineByKind[K]> = REGISTRIES[kind];
  const pipelines = usePipelines(kind);

  const configs = useSettingsStore((s) => s[PIPELINE_CONFIG_FIELDS[kind]]);
  const setPipelineConfig = useSettingsStore((s) => s.setPipelineConfig);
  const resetPipelineConfig = useSettingsStore((s) => s.resetPipelineConfig);

  const pipelineConfig = usePipelineConfig(kind, value === registry.auto ? null : value);
  const options = pipelines.map((pipeline) => ({ value: pipeline, ...registry.text(t, pipeline) }));
  // Widened from `K`, since the selector API cannot index a catalog by a type parameter.
  const stage: PipelineKind = kind;

  return (
    <LabeledOptionSelect
      icon={registry.icon}
      label={t(($) => $.documents.pipelines[stage])}
      className="w-35"
      value={value}
      options={options}
      onChange={onChange}
      placeholder={t(($) => $.documents.pipelines.select[stage])}
      disabled={disabled}
    >
      {pipelines.includes(value) && pipelineConfig && (
        <PipelineConfigDialog
          pipelineLabel={registry.text(t, value).label}
          pipelineType={kind}
          configSchema={pipelineConfig.schema}
          configDefaults={pipelineConfig.defaults}
          currentConfig={configs[value] ?? {}}
          onSave={(config) => setPipelineConfig(kind, value, config)}
          onReset={() => resetPipelineConfig(kind, value)}
          disabled={disabled}
        />
      )}
    </LabeledOptionSelect>
  );
}
