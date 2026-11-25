import { useQuery } from "@tanstack/react-query";
import { FileTextIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useRef } from "react";
import { useTranslation } from "react-i18next";

import { LoadingPanel } from "@/components/common/status-panel";
import { ScrollDialogContent } from "@/components/scroll-dialog-content";
import { TooltipButton } from "@/components/common/tooltip-button";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item";
import { Textarea } from "@/components/ui/textarea";
import { useConversationId } from "@/components/chat/chat-context";
import { documentQueries } from "@/api/documents";
import { basename, errorMessage } from "@/lib/utils";
import { type PassageGrade, type RagoldDraft, type RagoldDraftPassage } from "@/lib/ragold";
import {
  T_OPTIONS,
  useOptions,
  useTextSelection,
  AddButtons,
  ExcerptRow,
} from "@/components/chat/ragold/shared";

/**
 * Pick passages of *path* by selecting its text, one after another, and add
 * each with its grade, while *passageCount* tells how many it already has.
 */
export function PassageDialog({
  path,
  passageCount,
  onOpenChange,
  onAdd,
}: {
  path: string | null;
  passageCount: number;
  onOpenChange: (open: boolean) => void;
  onAdd: (text: string, grade: PassageGrade) => void;
}) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const options = useOptions();
  const conversationId = useConversationId();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const selection = useTextSelection(textarea);
  const content = useQuery({
    ...documentQueries.content(path ?? "", conversationId),
    enabled: path !== null,
  });

  const add = (grade: PassageGrade) => {
    onAdd(selection.text, grade);
    selection.clear();
  };

  return (
    <Dialog open={path !== null} onOpenChange={onOpenChange}>
      <ScrollDialogContent className="h-[80dvh] sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t(($) => $.addPassages)}</DialogTitle>
          <DialogDescription>
            {t(($) => $.passageDescription, { document: path ?? "" })}
          </DialogDescription>
        </DialogHeader>
        {content.isPending ? (
          <LoadingPanel />
        ) : (
          <Textarea
            ref={textarea}
            readOnly
            value={content.isError ? errorMessage(content.error) : content.data}
            onSelect={selection.onSelect}
            className="min-h-0 flex-1 resize-none font-mono text-sm"
          />
        )}
        <DialogFooter>
          <p aria-live="polite" className="text-sm text-muted-foreground sm:mr-auto sm:self-center">
            {t(($) => $.passageCount, { count: passageCount })}
          </p>
          <AddButtons options={options.grades} disabled={!selection.text} onAdd={add} />
          <DialogClose render={<Button />}>{t(($) => $.close)}</DialogClose>
        </DialogFooter>
      </ScrollDialogContent>
    </Dialog>
  );
}

/** A shipped document with its passages, whose grades can change, and actions to add or drop them. */
export function DocumentItem({
  path,
  passages,
  setDraft,
  onAddPassages,
}: {
  path: string;
  passages: RagoldDraftPassage[];
  setDraft: (update: (prev: RagoldDraft) => RagoldDraft) => void;
  onAddPassages: () => void;
}) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const options = useOptions();
  const updatePassages = (update: (passages: RagoldDraftPassage[]) => RagoldDraftPassage[]) =>
    setDraft((prev) => ({ ...prev, passages: update(prev.passages) }));

  return (
    <Item variant="outline" size="sm" className="flex-col items-stretch">
      <div className="flex items-center gap-2">
        <ItemMedia variant="icon">
          <FileTextIcon />
        </ItemMedia>
        <ItemContent className="min-w-0">
          <ItemTitle>{basename(path)}</ItemTitle>
          <ItemDescription className="truncate">{path}</ItemDescription>
        </ItemContent>
        <ItemActions>
          <TooltipButton label={t(($) => $.addPassages)} onClick={onAddPassages}>
            <PlusIcon />
          </TooltipButton>
          <TooltipButton
            label={t(($) => $.removeDocument)}
            destructive
            onClick={() =>
              setDraft((prev) => ({
                ...prev,
                documents: prev.documents.filter((item) => item !== path),
                passages: prev.passages.filter((item) => item.path !== path),
              }))
            }
          >
            <Trash2Icon />
          </TooltipButton>
        </ItemActions>
      </div>
      {passages.map((passage, index) => (
        <ExcerptRow
          key={index}
          className="pl-6"
          text={passage.text}
          value={passage.grade}
          options={options.grades}
          label={t(($) => $.grade)}
          removeLabel={t(($) => $.removePassage)}
          onChange={(grade) =>
            updatePassages((prev) =>
              prev.map((item) => (item === passage ? { ...item, grade } : item)),
            )
          }
          onRemove={() => updatePassages((prev) => prev.filter((item) => item !== passage))}
        />
      ))}
    </Item>
  );
}
