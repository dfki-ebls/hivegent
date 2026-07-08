import { Images } from "lucide-react";

import { featureFlags } from "@/lib/feature-flags";
import { AssetProcessingMode, type ChunkingPipeline, type ConversionPipeline } from "@/lib/types";
import { ChunkingPipelineSelector } from "@/components/ChunkingPipelineSelector";
import { ConversionPipelineSelector } from "@/components/ConversionPipelineSelector";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface PipelineSettingsBarProps {
  conversionPipeline: ConversionPipeline;
  chunkingPipeline: ChunkingPipeline;
  assetMode: AssetProcessingMode;
  onConversionPipelineChange: (pipeline: ConversionPipeline) => void;
  onChunkingPipelineChange: (pipeline: ChunkingPipeline) => void;
  onAssetModeChange: (mode: AssetProcessingMode) => void;
}

export function PipelineSettingsBar({
  conversionPipeline,
  chunkingPipeline,
  assetMode,
  onConversionPipelineChange,
  onChunkingPipelineChange,
  onAssetModeChange,
}: PipelineSettingsBarProps) {
  // The upload target lives in the drop zone now; this bar carries only the
  // pipeline/asset controls, so it renders nothing when both are flagged off.
  if (!featureFlags.pipelineSpec && !featureFlags.assetSpec) {
    return null;
  }

  return (
    <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-3 border-b px-4 py-3">
      {featureFlags.pipelineSpec && (
        <>
          <ConversionPipelineSelector
            value={conversionPipeline}
            onChange={onConversionPipelineChange}
          />
          <ChunkingPipelineSelector value={chunkingPipeline} onChange={onChunkingPipelineChange} />
        </>
      )}
      {featureFlags.assetSpec && (
        <div className="flex items-center gap-2">
          <Label
            htmlFor="asset-mode-select"
            className="text-sm text-muted-foreground flex items-center gap-1.5"
          >
            <Images className="h-4 w-4" />
            Assets
          </Label>
          <Select
            value={assetMode}
            onValueChange={(v) => onAssetModeChange(v as AssetProcessingMode)}
          >
            <SelectTrigger id="asset-mode-select" className="w-[120px]" size="sm">
              <SelectValue placeholder="Select mode" />
            </SelectTrigger>
            <SelectContent>
              {Object.values(AssetProcessingMode).map((mode) => (
                <SelectItem key={mode} value={mode}>
                  {mode[0].toUpperCase() + mode.slice(1)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  );
}
