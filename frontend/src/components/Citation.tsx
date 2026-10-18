import { FileTextIcon } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { ExtraProps } from "streamdown";
import { DocumentDialog } from "@/components/DocumentDialog";
import { DialogBody, ScrollDialogContent } from "@/components/ScrollDialogContent";
import { Button } from "@/components/ui/button";
import {
  CodeBlock,
  CodeBlockContent,
  CodeBlockHeader,
  CodeBlockTitle,
} from "@/components/ui/code-block";
import {
  Dialog,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDialogTarget } from "@/hooks/use-dialog-target";
import { keyPrefix } from "@/i18n";
import {
  chunkOriginLabel,
  chunkPositionLabel,
  type FetchedChunk,
  isLinePosition,
  lineBounds,
  type LinePosition,
  parseLinePositions,
} from "@/lib/types";
import { basename } from "@/lib/utils";
import { chunksForDocument, useFetchedDocumentsStore } from "@/stores/fetched-documents-store";

const T_OPTIONS = keyPrefix(($) => $.chat.citation);

/** The attributes of a `<cite>` tag in a reply, which Streamdown renders with this component. */
interface CitationProps extends ExtraProps {
  src?: string;
  line?: string;
}

interface Evidence {
  chunk: FetchedChunk;
  startLine: number;
  text: string;
}

/** The dialog a citation opens: the current document, or the evidence of one of its positions. */
type DialogTarget = "document" | number;

function evidenceFromChunk(chunk: FetchedChunk, position: LinePosition): Evidence | null {
  const contentLines = chunk.content.split("\n");
  const chunkBounds: [number, number] | undefined = isLinePosition(chunk.position)
    ? lineBounds(chunk.position)
    : chunk.position.type === "full_document"
      ? [1, contentLines.length]
      : undefined;
  if (!chunkBounds) return null;

  const [start, end] = lineBounds(position);
  const [chunkStart, chunkEnd] = chunkBounds;
  if (start < chunkStart || end > chunkEnd) return null;

  const firstIndex = start - chunkStart;
  const selected = contentLines.slice(firstIndex, firstIndex + end - start + 1);
  if (selected.length !== end - start + 1) return null;

  return { chunk, startLine: start, text: selected.join("\n") };
}

function evidenceForPosition(chunks: FetchedChunk[], position: LinePosition): Evidence[] {
  const unique = new Map<string, Evidence>();

  for (const chunk of chunks) {
    const evidence = evidenceFromChunk(chunk, position);
    if (!evidence) continue;
    if (!unique.has(evidence.text)) unique.set(evidence.text, evidence);
  }

  return [...unique.values()];
}

function EvidenceDialog({
  open,
  onOpenChange,
  filename,
  position,
  evidence,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filename: string;
  position: LinePosition;
  evidence: Evidence[];
}) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <ScrollDialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{filename}</DialogTitle>
          <DialogDescription>
            {t(($) => $.evidenceDescription, { position: chunkPositionLabel(position) })}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {evidence.map(({ chunk, startLine, text }) => (
            <CodeBlock key={chunk.id} size="sm">
              <CodeBlockHeader>
                <CodeBlockTitle>
                  {t(($) => $.capturedBy, { source: chunkOriginLabel(chunk) })}
                </CodeBlockTitle>
              </CodeBlockHeader>
              <CodeBlockContent lineNumbers startLine={startLine} wrap>
                {text}
              </CodeBlockContent>
            </CodeBlock>
          ))}
        </DialogBody>
      </ScrollDialogContent>
    </Dialog>
  );
}

export function Citation({ src, line }: CitationProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const { target, open, show, setOpen } = useDialogTarget<DialogTarget>("document");
  const chunks = useFetchedDocumentsStore((state) => state.chunks);
  // Subscribe to this document's entry rather than the whole map: its identity
  // changes only when the document itself gains a chunk, so a tool output for
  // some other file leaves every citation pointing at this one alone.
  const document = useFetchedDocumentsStore((state) =>
    src ? state.documents.get(src) : undefined,
  );

  const positions = useMemo(() => parseLinePositions(line), [line]);
  const documentChunks = useMemo(
    () => (document ? chunksForDocument(document, chunks) : []),
    [document, chunks],
  );
  const evidence = useMemo(
    () => positions.map((position) => evidenceForPosition(documentChunks, position)),
    [documentChunks, positions],
  );

  if (!src) return null;

  return (
    <span className="inline-flex flex-wrap items-center gap-0.5 align-middle">
      <Tooltip>
        <TooltipTrigger
          render={<Button variant="secondary" size="xs" onClick={() => show("document")} />}
        >
          <FileTextIcon data-icon="inline-start" />
          {basename(src)}
        </TooltipTrigger>
        <TooltipContent>{t(($) => $.openDocument)}</TooltipContent>
      </Tooltip>
      {positions.map((position, index) => {
        const available = evidence[index].length > 0;

        return (
          <Tooltip key={index}>
            <TooltipTrigger
              render={
                <Button
                  variant="outline"
                  size="xs"
                  disabled={!available}
                  focusableWhenDisabled
                  className="aria-disabled:opacity-50"
                  onClick={() => show(index)}
                />
              }
            >
              {chunkPositionLabel(position)}
            </TooltipTrigger>
            <TooltipContent>
              {available ? t(($) => $.openEvidence) : t(($) => $.noEvidence)}
            </TooltipContent>
          </Tooltip>
        );
      })}
      <DocumentDialog open={open && target === "document"} onOpenChange={setOpen} filename={src} />
      {typeof target === "number" && (
        <EvidenceDialog
          open={open}
          onOpenChange={setOpen}
          filename={src}
          position={positions[target]}
          evidence={evidence[target]}
        />
      )}
    </span>
  );
}
