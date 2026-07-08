import { FolderOpen, Images, Upload, X } from "lucide-react";

import { PERSONAL_SCOPE, splitScopePath } from "@/lib/api";
import { featureFlags } from "@/lib/feature-flags";
import { AssetProcessingMode, type ChunkingPipeline, type ConversionPipeline } from "@/lib/types";
import { ChunkingPipelineSelector } from "@/components/ChunkingPipelineSelector";
import { ConversionPipelineSelector } from "@/components/ConversionPipelineSelector";
import { Button } from "@/components/ui/button";
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
  /** Canonical target directory uploads and new documents land in. */
  target: string;
  /** Reset the target back to the personal workspace root. */
  onResetTarget: () => void;
  onConversionPipelineChange: (pipeline: ConversionPipeline) => void;
  onChunkingPipelineChange: (pipeline: ChunkingPipeline) => void;
  onAssetModeChange: (mode: AssetProcessingMode) => void;
}

/** Human-readable breadcrumb for a canonical target directory. */
function formatTarget(target: string): string {
  const { scope, local } = splitScopePath(target);
  const scopeLabel = scope === PERSONAL_SCOPE ? "Personal" : scope.slice(1);
  return local ? `${scopeLabel} / ${local.replaceAll("/", " / ")}` : scopeLabel;
}

export function PipelineSettingsBar({
  conversionPipeline,
  chunkingPipeline,
  assetMode,
  target,
  onResetTarget,
  onConversionPipelineChange,
  onChunkingPipelineChange,
  onAssetModeChange,
}: PipelineSettingsBarProps) {
  const atPersonalRoot = target === PERSONAL_SCOPE;

  return (
    <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-3 border-b px-4 py-3">
      <div className="flex items-center gap-2">
        <Label className="text-sm text-muted-foreground flex items-center gap-1.5">
          <Upload className="h-4 w-4" />
          Target
        </Label>
        <div
          className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-sm"
          title="Uploads, new documents and folders land here. Click a folder in the tree to change it."
        >
          <FolderOpen className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="font-medium">{formatTarget(target)}</span>
          {!atPersonalRoot && (
            <Button
              variant="ghost"
              size="icon"
              className="h-4 w-4"
              title="Reset to personal root"
              onClick={onResetTarget}
            >
              <X className="h-3 w-3" />
            </Button>
          )}
        </div>
      </div>
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
