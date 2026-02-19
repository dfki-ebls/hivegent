import { FileType } from "lucide-react";
import { useEffect, useState } from "react";

import { getConversionPipelines } from "../lib/api";
import type { ConversionPipeline, ConversionPipelineInfo } from "../lib/types";
import { Label } from "./ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";

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

  useEffect(() => {
    getConversionPipelines()
      .then(setPipelines)
      .catch(() => {
        // Silently fail — selector will be empty until pipelines load
      });
  }, []);

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
        onValueChange={(v) => onChange(v as ConversionPipeline)}
        disabled={disabled}
      >
        <SelectTrigger
          id="conversion-pipeline-select"
          className="w-[140px]"
          size="sm"
        >
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
    </div>
  );
}
