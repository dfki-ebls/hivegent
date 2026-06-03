import { type FetchedChunk, chunkPositionLabel } from "../../lib/types";
import { Badge } from "../ui/badge";

interface ChunkCardProps {
  chunk: FetchedChunk;
  onClick: () => void;
}

export function ChunkCard({ chunk, onClick }: ChunkCardProps) {
  return (
    <button
      type="button"
      className="ml-4 w-[calc(100%-1rem)] rounded-md border bg-card p-3 cursor-pointer transition-colors hover:bg-muted/50 text-left"
      onClick={onClick}
    >
      <div className="flex items-center gap-2 mb-1">
        <Badge variant="outline" className="text-xs">
          {chunk.source}
        </Badge>
        {chunk.score != null && (
          <Badge variant="secondary" className="text-xs">
            {(chunk.score * 100).toFixed(0)}%
          </Badge>
        )}
        <span className="text-xs text-muted-foreground">{chunkPositionLabel(chunk.position)}</span>
      </div>
      <p className="line-clamp-4 text-xs text-muted-foreground">{chunk.content.trim()}</p>
    </button>
  );
}
