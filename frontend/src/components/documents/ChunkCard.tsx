import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { type FetchedChunk, chunkOriginLabel, chunkPositionLabel } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ItemButton } from "@/components/RowButtons";
import { Badge } from "@/components/ui/badge";
import { ItemDescription, ItemHeader } from "@/components/ui/item";

/** A clickable outlined card of the context grid, shared by chunk and image cards. */
export function GridCard({ className, ...props }: ComponentProps<typeof ItemButton>) {
  return (
    <ItemButton
      variant="outline"
      size="xs"
      className={cn("min-w-0 content-start overflow-hidden", className)}
      {...props}
    />
  );
}

export function ChunkCard({ chunk, onClick }: { chunk: FetchedChunk; onClick: () => void }) {
  const { t } = useTranslation();
  const label = chunkPositionLabel(chunk.position);

  return (
    <GridCard
      title={t(($) => $.documents.chunkSummary, {
        origin: chunkOriginLabel(chunk),
        position: label,
      })}
      onClick={onClick}
    >
      <ItemHeader className="justify-start">
        <Badge variant="outline">{t(($) => $.documents.chunkOrigin[chunk.origin])}</Badge>
        <span className="min-w-0 truncate text-xs text-muted-foreground">{label}</span>
      </ItemHeader>
      <ItemDescription className="line-clamp-3 text-xs break-words">
        {chunk.content.trim()}
      </ItemDescription>
    </GridCard>
  );
}
