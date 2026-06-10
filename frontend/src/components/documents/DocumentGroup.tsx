import { ChevronRight, Globe } from "lucide-react";
import { useMemo, useState } from "react";

import { type FetchedChunk, type FetchedDocument, sortChunks } from "../../lib/types";
import { formatWebUrl, isWebUrl } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../ui/collapsible";
import { ChunkCard } from "./ChunkCard";
import { ImageThumb } from "./ImageThumb";

interface DocumentGroupProps {
  doc: FetchedDocument;
  chunks: FetchedChunk[];
  onChunkClick: (chunk: FetchedChunk) => void;
  onFilenameClick: (filename: string) => void;
}

export function DocumentGroup({ doc, chunks, onChunkClick, onFilenameClick }: DocumentGroupProps) {
  const [open, setOpen] = useState(true);
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
      (c) => !(c.position.type === "full_document" && c.source === "preview"),
    );
    return sortChunks(visible);
  }, [chunks]);

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
      </div>
      <CollapsibleContent>
        <div className="space-y-2 pb-2">
          {doc.image && <ImageThumb image={doc.image} />}
          {contentChunks.map((chunk) => (
            <ChunkCard key={chunk.id} chunk={chunk} onClick={() => onChunkClick(chunk)} />
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
