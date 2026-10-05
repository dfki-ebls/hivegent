import { Images } from "lucide-react";
import { useTranslation } from "react-i18next";

import { keyPrefix } from "@/i18n";
import { featureFlags } from "@/lib/feature-flags";
import { AssetProcessingMode, type ChunkingPipeline, type ConversionPipeline } from "@/lib/types";
import { PipelineSelector } from "@/components/PipelineSelector";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const T_OPTIONS = keyPrefix(($) => $.documents.pipelines);

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
  const { t } = useTranslation(undefined, T_OPTIONS);

  // The upload target lives in the drop zone now; this bar carries only the
  // pipeline/asset controls, so it renders nothing when both are flagged off.
  if (!featureFlags.pipelineSpec && !featureFlags.assetSpec) {
    return null;
  }

  return (
    <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-3 border-b px-4 py-3">
      {featureFlags.pipelineSpec && (
        <>
          <PipelineSelector
            kind="conversion"
            value={conversionPipeline}
            onChange={onConversionPipelineChange}
          />
          <PipelineSelector
            kind="chunking"
            value={chunkingPipeline}
            onChange={onChunkingPipelineChange}
          />
        </>
      )}
      {featureFlags.assetSpec && (
        <div className="flex items-center gap-2">
          <Label
            htmlFor="asset-mode-select"
            className="text-sm text-muted-foreground flex items-center gap-1.5"
          >
            <Images className="h-4 w-4" />
            {t(($) => $.assets)}
          </Label>
          <Select
            value={assetMode}
            onValueChange={(v) => onAssetModeChange(v as AssetProcessingMode)}
          >
            <SelectTrigger id="asset-mode-select" className="w-[120px]" size="sm">
              <SelectValue placeholder={t(($) => $.selectAssetMode)} />
            </SelectTrigger>
            <SelectContent>
              {Object.values(AssetProcessingMode).map((mode) => (
                <SelectItem key={mode} value={mode}>
                  {t(($) => $.assetMode[mode])}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  );
}
