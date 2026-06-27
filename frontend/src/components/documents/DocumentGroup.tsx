import { ChevronRight, Globe } from "lucide-react";
import { useMemo, useState } from "react";

import { featureFlags } from "../../lib/feature-flags";
import { type FetchedChunk, type FetchedDocument, sortChunks } from "../../lib/types";
import { formatWebUrl, isWebUrl } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../ui/collapsible";
import { AssetImage } from "./AssetImage";
import { ChunkCard } from "./ChunkCard";
import { DocumentMap } from "./DocumentMap";
import { documentReadMap } from "./utils";

interface DocumentGroupProps {
  doc: FetchedDocument;
  chunks: FetchedChunk[];
  onChunkClick: (chunk: FetchedChunk) => void;
  onFilenameClick: (filename: string) => void;
  onImageClick: (doc: FetchedDocument) => void;
}

export function DocumentGroup({
  doc,
  chunks,
  onChunkClick,
  onFilenameClick,
  onImageClick,
}: DocumentGroupProps) {
  const [open, setOpen] = useState(false);
  const isWeb = isWebUrl(doc.filename);
  // Image docs are keyed by their description path; show the image's own name.
  const displayName = doc.image
    ? (doc.image.filePath.split("/").pop() ?? doc.filename)
    : isWeb
      ? formatWebUrl(doc.filename)
      : doc.filename;
  const titlePath = doc.image?.filePath ?? doc.filename;

  // Include all chunks except user-initiated "preview" full-document fetches.
  // Model-fetched full documents appear as regular chunk cards (sorted first).
  const contentChunks = useMemo(() => {
    const visible = chunks.filter(
      (c) => !(c.position.type === "full_document" && c.origin === "preview"),
    );
    return sortChunks(visible);
  }, [chunks]);

  // A preview-fetched full document records the line count (doc.totalLines)
  // without being counted as a read span, so the map stays accurate once the
  // dialog opens.
  const mapSegments = useMemo(
    () => documentReadMap(contentChunks, doc.totalLines),
    [contentChunks, doc.totalLines],
  );

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className="flex items-center gap-2 px-1 py-2">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0">
            {isWeb && <Globe className="h-4 w-4" />}
            {!isWeb && (
              <ChevronRight className={`h-4 w-4 transition-transform ${open ? "rotate-90" : ""}`} />
            )}
          </Button>
        </CollapsibleTrigger>
        <button
          type="button"
          className="truncate text-sm font-medium hover:underline text-left min-w-0"
          onClick={() => {
            if (isWeb) {
              window.open(doc.filename, "_blank", "noopener,noreferrer");
            } else {
              onFilenameClick(doc.filename);
            }
          }}
          title={titlePath}
        >
          {displayName}
        </button>
        {contentChunks.length > 0 && (
          <Badge variant="outline" className="shrink-0 text-xs">
            {contentChunks.length} chunk
            {contentChunks.length !== 1 ? "s" : ""}
          </Badge>
        )}
        {featureFlags.documentMap && <DocumentMap segments={mapSegments} />}
      </div>
      <CollapsibleContent>
        <div className="@container ml-4 pb-2">
          <div className="grid auto-rows-fr grid-cols-2 gap-2 @sm:grid-cols-3 @lg:grid-cols-4">
            {doc.image && (
              <button
                type="button"
                onClick={() => onImageClick(doc)}
                title={doc.image.filePath}
                className="overflow-hidden rounded-md border cursor-pointer transition-colors hover:bg-muted/50"
              >
                <AssetImage
                  filePath={doc.image.filePath}
                  wrapperClassName={contentChunks.length > 0 ? "h-full w-full" : "aspect-square w-full"}
                  className="h-full w-full object-cover"
                />
              </button>
            )}
            {contentChunks.map((chunk) => (
              <ChunkCard key={chunk.id} chunk={chunk} onClick={() => onChunkClick(chunk)} />
            ))}
          </div>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
