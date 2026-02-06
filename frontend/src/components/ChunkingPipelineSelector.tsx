import { useEffect, useState } from 'react';
import { Scissors } from 'lucide-react';

import { getChunkingPipelines } from '../lib/api';
import type { ChunkingPipeline, ChunkingPipelineInfo } from '../lib/types';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

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

  useEffect(() => {
    getChunkingPipelines()
      .then(setPipelines)
      .catch(() => {
        // Silently fail — selector will be empty until pipelines load
      });
  }, []);

  return (
    <div className="flex items-center gap-2">
      <Label htmlFor="chunking-pipeline-select" className="text-sm text-muted-foreground flex items-center gap-1.5">
        <Scissors className="h-4 w-4" />
        Chunking
      </Label>
      <Select
        value={value}
        onValueChange={(v) => onChange(v as ChunkingPipeline)}
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
    </div>
  );
}
