import { Scissors } from "lucide-react";
import { useEffect, useState } from "react";

import { getChunkingPipelines } from "../lib/api";
import {
  ChunkingPipeline,
  type ChunkingPipelineInfo,
  ChunkingPipelineSchema,
} from "../lib/types";
import { useSettingsStore } from "../stores/settings-store";
import { PipelineConfigDialog } from "./PipelineConfigDialog";
import { Label } from "./ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";

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
    getChunkingPipelines()
      .then(setPipelines)
      .catch(() => {
        // Silently fail — selector will be empty until pipelines load
      });
  }, []);

  const selectedPipeline = pipelines.find((p) => p.value === value);
  const isAuto = value === ChunkingPipeline.AUTO;

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
        <SelectTrigger
          id="chunking-pipeline-select"
          className="w-[140px]"
          size="sm"
        >
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
      {selectedPipeline && !isAuto && (
        <PipelineConfigDialog
          pipelineLabel={selectedPipeline.label}
          pipelineType="chunking"
          configSchema={selectedPipeline.config_schema ?? {}}
          configDefaults={selectedPipeline.config_defaults ?? {}}
          currentConfig={chunkingConfigs[value] ?? {}}
          onSave={(config) => setChunkingConfig(value, config)}
          onReset={() => resetChunkingConfig(value)}
          disabled={disabled}
        />
      )}
    </div>
  );
}
