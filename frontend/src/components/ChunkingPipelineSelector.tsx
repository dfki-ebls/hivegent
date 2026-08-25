import { Scissors } from "lucide-react";
import { useEffect, useState } from "react";

import { getChunkingPipelineConfig, listChunkingPipelines } from "@/lib/api";
import { ChunkingPipeline, type ChunkingPipelineInfo, ChunkingPipelineSchema } from "@/lib/types";
import { usePipelineConfig } from "@/hooks/use-pipeline-config";
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

interface ChunkingPipelineSelectorProps {
  value: ChunkingPipeline;
  onChange: (value: ChunkingPipeline) => void;
  disabled?: boolean;
}

export function ChunkingPipelineSelector({
  value,
  onChange,
  disabled,
}: ChunkingPipelineSelectorProps) {
  const [pipelines, setPipelines] = useState<ChunkingPipelineInfo[]>([]);

  const chunkingConfigs = useSettingsStore((s) => s.chunkingConfigs);
  const setChunkingConfig = useSettingsStore((s) => s.setChunkingConfig);
  const resetChunkingConfig = useSettingsStore((s) => s.resetChunkingConfig);

  useEffect(() => {
    listChunkingPipelines()
      .then(setPipelines)
      .catch(() => {
        // Silently fail — selector will be empty until pipelines load
      });
  }, []);

  const selectedPipeline = pipelines.find((p) => p.value === value);
  const pipelineConfig = usePipelineConfig(
    value === ChunkingPipeline.AUTO ? null : value,
    getChunkingPipelineConfig,
  );

  return (
    <div className="flex items-center gap-2">
      <Label
        htmlFor="chunking-pipeline-select"
        className="text-sm text-muted-foreground flex items-center gap-1.5"
      >
        <Scissors className="h-4 w-4" />
        Chunking
      </Label>
      <Select
        value={value}
        onValueChange={(v) => {
          const parsed = ChunkingPipelineSchema.safeParse(v);
          if (parsed.success) onChange(parsed.data);
        }}
        disabled={disabled}
      >
        <SelectTrigger id="chunking-pipeline-select" className="w-[140px]" size="sm">
          <SelectValue placeholder="Select chunking" />
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
          pipelineType="chunking"
          configSchema={pipelineConfig.schema}
          configDefaults={pipelineConfig.defaults}
          currentConfig={chunkingConfigs[value] ?? {}}
          onSave={(config) => setChunkingConfig(value, config)}
          onReset={() => resetChunkingConfig(value)}
          disabled={disabled}
        />
      )}
    </div>
  );
}
