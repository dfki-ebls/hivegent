import { type QueryObserverResult, useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { FileTextIcon, PlusIcon, SendIcon, Trash2Icon } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { LoadingState } from "@/components/EmptyState";
import { OptionSelect } from "@/components/OptionSelect";
import { DialogBody, ScrollDialogContent } from "@/components/ScrollDialogContent";
import { TooltipButton } from "@/components/TooltipButton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/ui/toast";
import { useChatMessages } from "@/hooks/chat/use-chat-messages";
import { useConversationId } from "@/hooks/chat/use-conversation-id";
import { i18n, keyPrefix } from "@/i18n";
import {
  canonicalPath,
  exportRagold,
  groupScope,
  isWorkspacePath,
  PERSONAL_SCOPE,
} from "@/lib/api";
import { precedingUserText } from "@/lib/chat/chat-utils";
import {
  ANSWERABILITIES,
  draftFromTurn,
  PASSAGE_GRADES,
  type PassageGrade,
  QUESTION_TYPES,
  type RagoldDraft,
  type RagoldPassage,
  RagoldTransferError,
  sendToRagold,
  withDocument,
  withPassage,
} from "@/lib/ragold";
import type { DirectoryTreeResponse } from "@/lib/types";
import { basename, collectFilePaths, errorMessage } from "@/lib/utils";
import { documentQueries } from "@/queries/documents";
import { useServerSettings } from "@/queries/settings";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";

const T_OPTIONS = keyPrefix(($) => $.chat.ragold);

/** Most matches a document picker renders, so thousands of documents stay fast to search. */
const PICKER_LIMIT = 50;

function useOptions() {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return useMemo(
    () => ({
      questionTypes: QUESTION_TYPES.map((value) => ({
        value,
        label: t(($) => $.questionTypes[value]),
      })),
      answerabilities: ANSWERABILITIES.map((value) => ({
        value,
        label: t(($) => $.answerabilities[value]),
      })),
      grades: PASSAGE_GRADES.map((value) => ({ value, label: t(($) => $.grades[value]) })),
    }),
    [t],
  );
}

/**
 * Every document of the workspaces the user can read, led by the *used* ones
 * the tool outputs of this conversation captured.
 */
function usePickerDocuments(): { paths: string[]; used: Set<string> } {
  const documents = useFetchedDocumentsStore((state) => state.documents);
  const groups = useServerSettings((settings) => settings.user.groups);
  const scopes = useMemo(
    () => [PERSONAL_SCOPE, ...(groups ?? []).map((group) => groupScope(group.id))],
    [groups],
  );
  // Stable, so a render leaves the flattened paths alone until a tree changes.
  const combine = useCallback(
    (results: QueryObserverResult<DirectoryTreeResponse>[]) =>
      results.flatMap((result, index) =>
        result.data
          ? collectFilePaths(result.data.root).map((path) => canonicalPath(scopes[index], path))
          : [],
      ),
    [scopes],
  );

  const collection = useQueries({
    queries: scopes.map((scope) => documentQueries.tree(scope)),
    combine,
  });

  return useMemo(() => {
    const used = new Set([...documents.keys()].filter(isWorkspacePath).sort());

    return { paths: [...new Set([...used, ...collection])], used };
  }, [documents, collection]);
}

/** A searchable list of the documents that renders a bounded number of matches. */
function DocumentPicker({ onPick }: { onPick: (path: string) => void }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const { paths, used } = usePickerDocuments();

  return (
    <Combobox<string>
      items={paths}
      value={null}
      limit={PICKER_LIMIT}
      onValueChange={(path) => path && onPick(path)}
    >
      <ComboboxInput placeholder={t(($) => $.searchDocuments)} />
      <ComboboxContent>
        <ComboboxEmpty>{t(($) => $.noMatches)}</ComboboxEmpty>
        <ComboboxList>
          {(path: string) => (
            <ComboboxItem key={path} value={path}>
              <span className="truncate">{path}</span>
              {used.has(path) && (
                <Badge variant="secondary" className="ml-auto">
                  {t(($) => $.used)}
                </Badge>
              )}
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}

/**
 * Pick passages of *path* by selecting its text, one after another, and add
 * each with its grade, while *passageCount* tells how many it already has.
 */
function PassageDialog({
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
  const [selection, setSelection] = useState("");
  const [grade, setGrade] = useState<PassageGrade>("needed");
  const content = useQuery({
    ...documentQueries.content(path ?? "", conversationId),
    enabled: path !== null,
  });

  const add = () => {
    onAdd(selection, grade);
    setSelection("");
    textarea.current?.setSelectionRange(
      textarea.current.selectionEnd,
      textarea.current.selectionEnd,
    );
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
          <LoadingState />
        ) : (
          <Textarea
            ref={textarea}
            readOnly
            value={content.isError ? errorMessage(content.error) : content.data}
            onSelect={({ currentTarget: { value, selectionStart, selectionEnd } }) =>
              setSelection(value.slice(selectionStart, selectionEnd).trim())
            }
            className="min-h-0 flex-1 resize-none font-mono text-sm"
          />
        )}
        <DialogFooter>
          <p aria-live="polite" className="text-sm text-muted-foreground sm:mr-auto sm:self-center">
            {t(($) => $.passageCount, { count: passageCount })}
          </p>
          <OptionSelect value={grade} options={options.grades} onChange={setGrade} />
          <Button variant="outline" disabled={!selection} onClick={add}>
            <PlusIcon data-icon="inline-start" />
            {t(($) => $.addSelection)}
          </Button>
          <DialogClose render={<Button />}>{t(($) => $.close)}</DialogClose>
        </DialogFooter>
      </ScrollDialogContent>
    </Dialog>
  );
}

function transferErrorMessage(error: unknown): string {
  if (!(error instanceof RagoldTransferError)) return errorMessage(error);

  const message = i18n.t(($) => $.chat.ragold[error.reason]);

  return error.message ? `${message}: ${error.message}` : message;
}

/** A shipped document with its passages, whose grades can change, and actions to add or drop them. */
function DocumentItem({
  path,
  passages,
  setDraft,
  onAddPassages,
}: {
  path: string;
  passages: RagoldPassage[];
  setDraft: (update: (prev: RagoldDraft) => RagoldDraft) => void;
  onAddPassages: () => void;
}) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const options = useOptions();
  const updatePassages = (update: (passages: RagoldPassage[]) => RagoldPassage[]) =>
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
        <div key={index} className="flex items-start gap-2 pl-6">
          <p className="line-clamp-4 min-w-0 flex-1 font-mono text-xs whitespace-pre-wrap">
            {passage.text}
          </p>
          <OptionSelect
            size="sm"
            aria-label={t(($) => $.grade)}
            value={passage.grade}
            options={options.grades}
            onChange={(grade) =>
              updatePassages((prev) =>
                prev.map((item) => (item === passage ? { ...item, grade } : item)),
              )
            }
          />
          <TooltipButton
            label={t(($) => $.removePassage)}
            destructive
            onClick={() => updatePassages((prev) => prev.filter((item) => item !== passage))}
          >
            <Trash2Icon />
          </TooltipButton>
        </div>
      ))}
    </Item>
  );
}

interface RagoldFormProps {
  messageId: string;
  reply: string;
  ragoldUrl: string;
  conversationId: string;
  onClose: () => void;
}

/** The entry of one turn, prefilled from the reply and its citations, and its transfer. */
function RagoldForm({ messageId, reply, ragoldUrl, conversationId, onClose }: RagoldFormProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const options = useOptions();
  const messages = useChatMessages();
  const [draft, setDraft] = useState(() => {
    const { documents, chunks } = useFetchedDocumentsStore.getState();

    return draftFromTurn(precedingUserText(messages, messageId), reply, documents, chunks);
  });
  const [passageFor, setPassageFor] = useState<string | null>(null);
  const passagesByPath = Map.groupBy(draft.passages, (passage) => passage.path);
  const update = (changes: Partial<RagoldDraft>) => setDraft((prev) => ({ ...prev, ...changes }));
  const send = useMutation({
    mutationFn: (tab: Window) => {
      const origin = new URL(`/conversations/${conversationId}`, window.location.origin).href;
      const zip = exportRagold(conversationId, { ...draft, origin });

      return sendToRagold(tab, new URL(ragoldUrl).origin, zip);
    },
    onSuccess: (count) => {
      toast.add({ type: "success", title: t(($) => $.done, { count: count ?? 1 }) });
      onClose();
    },
    onError: (error) => toast.add({ type: "error", title: transferErrorMessage(error) }),
  });

  // Opened in the click itself, so popup blockers let it through.
  const transfer = () => {
    const tab = window.open(`${ragoldUrl}#import`);

    if (!tab) {
      toast.add({ type: "error", title: t(($) => $.popupBlocked) });
      return;
    }

    send.mutate(tab);
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t(($) => $.action)}</DialogTitle>
        <DialogDescription>{t(($) => $.description)}</DialogDescription>
      </DialogHeader>
      <DialogBody>
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="ragold-query">{t(($) => $.query)}</FieldLabel>
            <Textarea
              id="ragold-query"
              value={draft.query}
              onChange={(event) => update({ query: event.target.value })}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="ragold-response">{t(($) => $.response)}</FieldLabel>
            <Textarea
              id="ragold-response"
              value={draft.response}
              onChange={(event) => update({ response: event.target.value })}
              className="max-h-64"
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="ragold-question-type">{t(($) => $.questionType)}</FieldLabel>
              <OptionSelect
                id="ragold-question-type"
                value={draft.questionType}
                options={options.questionTypes}
                onChange={(questionType) => update({ questionType })}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="ragold-answerability">{t(($) => $.answerability)}</FieldLabel>
              <OptionSelect
                id="ragold-answerability"
                value={draft.answerability}
                options={options.answerabilities}
                onChange={(answerability) => update({ answerability })}
              />
            </Field>
          </div>
          <FieldSet>
            <FieldLegend variant="label">{t(($) => $.documents)}</FieldLegend>
            <DocumentPicker onPick={(path) => setDraft((prev) => withDocument(prev, path))} />
            {draft.documents.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t(($) => $.noDocuments)}</p>
            ) : (
              <ItemGroup>
                {draft.documents.map((path) => (
                  <DocumentItem
                    key={path}
                    path={path}
                    passages={passagesByPath.get(path) ?? []}
                    setDraft={setDraft}
                    onAddPassages={() => setPassageFor(path)}
                  />
                ))}
              </ItemGroup>
            )}
          </FieldSet>
          <Field>
            <FieldLabel htmlFor="ragold-notes">{t(($) => $.notes)}</FieldLabel>
            <Textarea
              id="ragold-notes"
              value={draft.notes}
              onChange={(event) => update({ notes: event.target.value })}
            />
          </Field>
        </FieldGroup>
      </DialogBody>
      <DialogFooter>
        <DialogClose render={<Button variant="outline" />}>
          {i18n.t(($) => $.common.actions.cancel)}
        </DialogClose>
        <Button
          disabled={send.isPending || !draft.query.trim() || !draft.response.trim()}
          onClick={transfer}
        >
          {send.isPending ? (
            <Spinner data-icon="inline-start" />
          ) : (
            <SendIcon data-icon="inline-start" />
          )}
          {t(($) => $.transfer)}
        </Button>
      </DialogFooter>
      <PassageDialog
        path={passageFor}
        passageCount={passageFor ? (passagesByPath.get(passageFor)?.length ?? 0) : 0}
        onOpenChange={(open) => !open && setPassageFor(null)}
        onAdd={(text, grade) =>
          passageFor && setDraft((prev) => withPassage(prev, { path: passageFor, text, grade }))
        }
      />
    </>
  );
}

/**
 * The action that hands a reply to RAGold as a gold benchmark entry, shown only
 * when the deployment names a RAGold and the conversation is persisted.
 */
export function RagoldAction({ messageId, reply }: { messageId: string; reply: string }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const ragoldUrl = useServerSettings((settings) => settings.ragold_url);
  const conversationId = useConversationId();
  const [open, setOpen] = useState(false);

  if (!ragoldUrl || !conversationId) return null;

  return (
    <>
      <TooltipButton label={t(($) => $.action)} onClick={() => setOpen(true)}>
        <SendIcon />
      </TooltipButton>
      <Dialog open={open} onOpenChange={setOpen}>
        <ScrollDialogContent className="sm:max-w-3xl">
          <RagoldForm
            messageId={messageId}
            reply={reply}
            ragoldUrl={ragoldUrl}
            conversationId={conversationId}
            onClose={() => setOpen(false)}
          />
        </ScrollDialogContent>
      </Dialog>
    </>
  );
}
