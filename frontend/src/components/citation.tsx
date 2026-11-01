import { FileTextIcon } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { ExtraProps } from "streamdown";
import { DocumentDialog } from "@/components/document-dialog";
import { DialogBody, ScrollDialogContent } from "@/components/scroll-dialog-content";
import { Button } from "@/components/ui/button";
import {
  CodeBlock,
  CodeBlockContent,
  CodeBlockHeader,
  CodeBlockTitle,
} from "@/components/ui/code-block";
import { Dialog, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDialogTarget } from "@/hooks/use-dialog-target";
import { keyPrefix } from "@/i18n";
import { type ResolvedPosition, resolveCitation } from "@/lib/citations";
import { chunkOriginLabel, chunkPositionLabel } from "@/lib/chunks";
import { basename } from "@/lib/utils";
import { chunksForDocument, useFetchedDocumentsStore } from "@/stores/fetched-documents-store";

const T_OPTIONS = keyPrefix(($) => $.chat.citation);

/** The attributes of a `<cite>` tag in a reply, which Streamdown renders with this component. */
interface CitationProps extends ExtraProps {
  src?: string;
  line?: string;
}

/** The dialog a citation opens: the current document, or the evidence of one of its positions. */
type DialogTarget = "document" | number;

function EvidenceDialog({
  open,
  onOpenChange,
  filename,
  resolved: { position, evidence },
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filename: string;
  resolved: ResolvedPosition;
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

  const documentChunks = useMemo(
    () => (document ? chunksForDocument(document, chunks) : []),
    [document, chunks],
  );
  const positions = useMemo(() => resolveCitation(documentChunks, line), [documentChunks, line]);
  const resolved = typeof target === "number" ? positions[target] : undefined;

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
      {positions.map(({ position, evidence }, index) => {
        if (evidence.length === 0) return null;

        return (
          <Tooltip key={index}>
            <TooltipTrigger
              render={<Button variant="outline" size="xs" onClick={() => show(index)} />}
            >
              {chunkPositionLabel(position)}
            </TooltipTrigger>
            <TooltipContent>{t(($) => $.openEvidence)}</TooltipContent>
          </Tooltip>
        );
      })}
      <DocumentDialog open={open && target === "document"} onOpenChange={setOpen} filename={src} />
      {resolved && (
        <EvidenceDialog open={open} onOpenChange={setOpen} filename={src} resolved={resolved} />
      )}
    </span>
  );
}
