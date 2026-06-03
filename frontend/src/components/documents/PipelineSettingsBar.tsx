import { Images, Upload } from "lucide-react";

import { PERSONAL_SCOPE, groupScope } from "../../lib/api";
import { featureFlags } from "../../lib/feature-flags";
import {
  AssetProcessingMode,
  type ChunkingPipeline,
  type ConversionPipeline,
} from "../../lib/types";
import { ChunkingPipelineSelector } from "../ChunkingPipelineSelector";
import { ConversionPipelineSelector } from "../ConversionPipelineSelector";
import { Label } from "../ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";

interface PipelineSettingsBarProps {
  conversionPipeline: ConversionPipeline;
  chunkingPipeline: ChunkingPipeline;
  assetMode: AssetProcessingMode;
  /** Active upload target: `~` for personal, `@<group>` for a group. */
  uploadScope: string;
  /** Groups the user can upload to. */
  writableGroups: string[];
  onUploadScopeChange: (scope: string) => void;
  onConversionPipelineChange: (pipeline: ConversionPipeline) => void;
  onChunkingPipelineChange: (pipeline: ChunkingPipeline) => void;
  onAssetModeChange: (mode: AssetProcessingMode) => void;
}

export function PipelineSettingsBar({
  conversionPipeline,
  chunkingPipeline,
  assetMode,
  uploadScope,
  writableGroups,
  onUploadScopeChange,
  onConversionPipelineChange,
  onChunkingPipelineChange,
  onAssetModeChange,
}: PipelineSettingsBarProps) {
  return (
    <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-3 border-b px-4 py-3">
      {writableGroups.length > 0 && (
        <div className="flex items-center gap-2">
          <Label
            htmlFor="upload-scope-select"
            className="text-sm text-muted-foreground flex items-center gap-1.5"
          >
            <Upload className="h-4 w-4" />
            Upload to
          </Label>
          <Select value={uploadScope} onValueChange={onUploadScopeChange}>
            <SelectTrigger id="upload-scope-select" className="w-[140px]" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={PERSONAL_SCOPE}>Personal</SelectItem>
              {writableGroups.map((g) => (
                <SelectItem key={g} value={groupScope(g)}>
                  {g}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      {featureFlags.pipelineSpec && (
        <>
          <ConversionPipelineSelector
            value={conversionPipeline}
            onChange={onConversionPipelineChange}
          />
          <ChunkingPipelineSelector value={chunkingPipeline} onChange={onChunkingPipelineChange} />
        </>
      )}
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
    </div>
  );
}
