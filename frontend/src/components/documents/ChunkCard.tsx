import { type FetchedChunk, chunkOriginLabel, chunkPositionLabel } from "../../lib/types";
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
      title={`${chunkOriginLabel(chunk)} — ${label}`}
      className="flex min-w-0 flex-col gap-1 overflow-hidden rounded-md border bg-card p-2 text-left cursor-pointer transition-colors hover:bg-muted/50"
    >
      <div className="flex min-w-0 items-center gap-1.5">
        <Badge variant="outline" className="shrink-0 text-[10px] capitalize">
          {chunk.origin}
        </Badge>
        <span className="truncate text-[10px] text-muted-foreground">{label}</span>
      </div>
      <p className="line-clamp-3 break-words text-[11px] leading-snug text-muted-foreground">
        {chunk.content.trim()}
      </p>
    </button>
  );
}
