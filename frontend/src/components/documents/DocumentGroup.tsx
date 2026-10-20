import { Globe } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { type FetchedChunk, type FetchedDocument, sortChunks } from "@/lib/types";
import { basename, formatWebUrl, isWebUrl } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { AssetMedia } from "@/components/documents/AssetMedia";
import { ChunkCard, GridCard } from "@/components/documents/ChunkCard";
import { DocumentMap } from "@/components/documents/DocumentMap";
import { ExpandButton, NameButton } from "@/components/RowButtons";

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
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const isWeb = isWebUrl(doc.filename);
  // Image docs are keyed by their description path; show the image's own name.
  const displayName = doc.image
    ? basename(doc.image.filePath)
    : isWeb
      ? formatWebUrl(doc.filename)
      : doc.filename;
  const titlePath = doc.image?.filePath ?? doc.filename;

  // Full documents appear as regular chunk cards, sorted first.
  const contentChunks = useMemo(() => sortChunks(chunks), [chunks]);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className="flex items-center gap-2 py-1">
        <CollapsibleTrigger render={<ExpandButton expanded={open} />} />
        {isWeb && <Globe className="size-4 shrink-0 text-muted-foreground" />}
        {isWeb ? (
          <NameButton
            nativeButton={false}
            // oxlint-disable-next-line jsx-a11y/control-has-associated-label -- the button renders its label into the link
            render={<a href={doc.filename} target="_blank" rel="noreferrer" />}
            title={titlePath}
          >
            {displayName}
          </NameButton>
        ) : (
          <NameButton title={titlePath} onClick={() => onFilenameClick(doc.filename)}>
            {displayName}
          </NameButton>
        )}
        {contentChunks.length > 0 && (
          <Badge variant="outline">
            {t(($) => $.documents.chunks, { count: contentChunks.length })}
          </Badge>
        )}
        <DocumentMap filename={doc.filename} chunks={contentChunks} totalLines={doc.totalLines} />
      </div>
      <CollapsibleContent className="@container pb-2 pl-8">
        <div className="grid auto-rows-fr grid-cols-2 gap-2 @sm:grid-cols-3 @lg:grid-cols-4">
          {doc.image && (
            <GridCard title={doc.image.filePath} className="p-0" onClick={() => onImageClick(doc)}>
              <AssetMedia
                filePath={doc.image.filePath}
                mediaType={doc.image.mediaType}
                wrapperClassName={contentChunks.length > 0 ? "size-full" : "aspect-square w-full"}
                className="size-full object-cover"
              />
            </GridCard>
          )}
          {contentChunks.map((chunk) => (
            <ChunkCard key={chunk.id} chunk={chunk} onClick={() => onChunkClick(chunk)} />
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
