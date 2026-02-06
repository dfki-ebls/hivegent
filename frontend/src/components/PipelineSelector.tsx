import { useEffect, useState } from 'react';
import { FileType } from 'lucide-react';

import { getConversionPipelines } from '../lib/api';
import type { ConversionPipeline, ConversionPipelineInfo } from '../lib/types';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

interface PipelineSelectorProps {
  value: ConversionPipeline;
  onChange: (value: ConversionPipeline) => void;
  disabled?: boolean;
}

export function PipelineSelector({
  value,
  onChange,
  disabled,
}: PipelineSelectorProps) {
  const [pipelines, setPipelines] = useState<ConversionPipelineInfo[]>([]);

  useEffect(() => {
    getConversionPipelines()
      .then(setPipelines)
      .catch(() => {
        // Silently fail — selector will be empty until pipelines load
      });
  }, []);

  return (
    <div className="flex items-center gap-2">
      <Label htmlFor="pipeline-select" className="text-sm text-muted-foreground flex items-center gap-1.5">
        <FileType className="h-4 w-4" />
        Pipeline
      </Label>
      <Select
        value={value}
        onValueChange={(v) => onChange(v as ConversionPipeline)}
        disabled={disabled}
      >
        <SelectTrigger id="pipeline-select" className="w-[140px]" size="sm">
          <SelectValue placeholder="Select pipeline" />
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
