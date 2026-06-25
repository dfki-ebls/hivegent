import { type FetchedChunk, chunkPositionLabel, chunkSourceLabel } from "../../lib/types";
import { Badge } from "../ui/badge";

interface ChunkCardProps {
  chunk: FetchedChunk;
  onClick: () => void;
}

export function ChunkCard({ chunk, onClick }: ChunkCardProps) {
  const label = chunkPositionLabel(chunk.position);

  return (
    <button
      type="button"
      onClick={onClick}
      title={`${chunkSourceLabel(chunk)} — ${label}`}
      className="flex min-w-0 items-center gap-1.5 rounded-md border bg-card px-2 py-1.5 text-left cursor-pointer transition-colors hover:bg-muted/50"
    >
      <Badge variant="outline" className="shrink-0 text-[10px] capitalize">
        {chunk.tool}
      </Badge>
      <span className="truncate text-[11px] text-muted-foreground">{label}</span>
    </button>
  );
}
