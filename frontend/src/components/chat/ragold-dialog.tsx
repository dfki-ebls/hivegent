import { type QueryObserverResult, useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { FileTextIcon, PlusIcon, SendIcon, Trash2Icon } from "lucide-react";
import {
  type RefObject,
  type SyntheticEvent,
  useCallback,
  useMemo,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";

import { LoadingState } from "@/components/empty-state";
import { type Option, OptionSelect } from "@/components/option-select";
import { DialogBody, ScrollDialogContent } from "@/components/scroll-dialog-content";
import { TooltipButton } from "@/components/tooltip-button";
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
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
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
  completeEntry,
  draftFromTurn,
  KEY_FACT_IMPORTANCES,
  PASSAGE_GRADES,
  type PassageGrade,
  QUESTION_TYPES,
  type RagoldDraft,
  type RagoldDraftPassage,
  type RagoldEntry,
  RagoldTransferError,
  sendToRagold,
  withDocument,
  withKeyFact,
  withPassage,
} from "@/lib/ragold";
import type { DirectoryTreeResponse } from "@/lib/types";
import { basename, cn, collectFilePaths, errorMessage } from "@/lib/utils";
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
      importances: KEY_FACT_IMPORTANCES.map((value) => ({
        value,
        label: t(($) => $.importances[value]),
      })),
    }),
    [t],
  );
}

/** The trimmed text selected in *ref*, and a way to collapse the selection once it is used. */
function useTextSelection(ref: RefObject<HTMLTextAreaElement | null>) {
  const [text, setText] = useState("");

  const onSelect = ({
    currentTarget: { value, selectionStart, selectionEnd },
  }: SyntheticEvent<HTMLTextAreaElement>) => setText(value.slice(selectionStart, selectionEnd).trim());

  const clear = () => {
    setText("");
    ref.current?.setSelectionRange(ref.current.selectionEnd, ref.current.selectionEnd);
  };

  return { text, onSelect, clear };
}

/** One button per option that adds the selected text with that option, so each pick is explicit. */
function AddButtons<T extends string>({
  options,
  disabled,
  onAdd,
}: {
  options: readonly Option<T>[];
  disabled: boolean;
  onAdd: (value: T) => void;
}) {
  return options.map((option) => (
    <Button
      key={option.value}
      variant="outline"
      disabled={disabled}
      onClick={() => onAdd(option.value)}
    >
      <PlusIcon data-icon="inline-start" />
      {option.label}
    </Button>
  ));
}

/** An excerpt with its choice, such as a passage with its grade, and an action to remove it. */
function ExcerptRow<T extends string>({
  text,
  value,
  options,
  label,
  removeLabel,
  error,
  className,
  onChange,
  onRemove,
}: {
  className?: string;
  text: string;
  value: T | null;
  options: readonly Option<T>[];
  label: string;
  removeLabel: string;
  error?: string;
  onChange: (value: T) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <div className={cn("flex items-start gap-2", className)}>
      <div className="min-w-0 flex-1">
        <p className="line-clamp-4 font-mono text-xs whitespace-pre-wrap">{text}</p>
        {error && <FieldError>{error}</FieldError>}
      </div>
      <OptionSelect
        size="sm"
        aria-label={label}
        value={value}
        options={options}
        placeholder={t(($) => $.choose)}
        onChange={onChange}
      />
      <TooltipButton label={removeLabel} destructive onClick={onRemove}>
        <Trash2Icon />
      </TooltipButton>
    </div>
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
          <LoadingState />
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

interface RagoldFormProps {
  messageId: string;
  reply: string;
  ragoldUrl: string;
  conversationId: string;
  onClose: () => void;
}

/** Key facts marked by selecting text of the response, each with its importance. */
function KeyFactsField({
  draft,
  setDraft,
}: {
  draft: RagoldDraft;
  setDraft: (update: (prev: RagoldDraft) => RagoldDraft) => void;
}) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const options = useOptions();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const selection = useTextSelection(textarea);
  const updateKeyFacts = (
    update: (keyFacts: RagoldDraft["keyFacts"]) => RagoldDraft["keyFacts"],
  ) => setDraft((prev) => ({ ...prev, keyFacts: update(prev.keyFacts) }));

  return (
    <>
      <Field>
        <FieldLabel htmlFor="ragold-response">{t(($) => $.response)}</FieldLabel>
        <Textarea
          ref={textarea}
          id="ragold-response"
          value={draft.response}
          onChange={(event) => setDraft((prev) => ({ ...prev, response: event.target.value }))}
          onSelect={selection.onSelect}
          className="max-h-64"
        />
      </Field>
      <FieldSet>
        <FieldLegend variant="label">{t(($) => $.keyFacts)}</FieldLegend>
        <FieldDescription>{t(($) => $.keyFactsDescription)}</FieldDescription>
        <div className="flex flex-wrap gap-2">
          <AddButtons
            options={options.importances}
            disabled={!selection.text}
            onAdd={(importance) => {
              setDraft((prev) => withKeyFact(prev, { text: selection.text, importance }));
              selection.clear();
            }}
          />
        </div>
        {draft.keyFacts.map((fact) => (
          <ExcerptRow
            key={fact.text}
            text={fact.text}
            value={fact.importance}
            options={options.importances}
            label={t(($) => $.importance)}
            removeLabel={t(($) => $.removeKeyFact)}
            error={draft.response.includes(fact.text) ? undefined : t(($) => $.staleKeyFact)}
            onChange={(importance) =>
              updateKeyFacts((prev) =>
                prev.map((item) => (item === fact ? { ...item, importance } : item)),
              )
            }
            onRemove={() => updateKeyFacts((prev) => prev.filter((item) => item !== fact))}
          />
        ))}
      </FieldSet>
    </>
  );
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
  const entry = completeEntry(draft);
  const send = useMutation({
    mutationFn: ({ tab, entry }: { tab: Window; entry: RagoldEntry }) => {
      const origin = new URL(`/conversations/${conversationId}`, window.location.origin).href;
      const zip = exportRagold(conversationId, { ...entry, origin });

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
    if (!entry) return;

    const tab = window.open(`${ragoldUrl}#import`);

    if (!tab) {
      toast.add({ type: "error", title: t(($) => $.popupBlocked) });
      return;
    }

    send.mutate({ tab, entry });
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
          <KeyFactsField draft={draft} setDraft={setDraft} />
          <div className="grid gap-4 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="ragold-question-type">{t(($) => $.questionType)}</FieldLabel>
              <OptionSelect
                id="ragold-question-type"
                value={draft.questionType}
                options={options.questionTypes}
                placeholder={t(($) => $.choose)}
                onChange={(questionType) => update({ questionType })}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="ragold-answerability">{t(($) => $.answerability)}</FieldLabel>
              <OptionSelect
                id="ragold-answerability"
                value={draft.answerability}
                options={options.answerabilities}
                placeholder={t(($) => $.choose)}
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
        {!entry && (
          <p className="text-sm text-muted-foreground sm:mr-auto sm:self-center">
            {t(($) => $.incomplete)}
          </p>
        )}
        <DialogClose render={<Button variant="outline" />}>
          {i18n.t(($) => $.common.actions.cancel)}
        </DialogClose>
        <Button disabled={send.isPending || !entry} onClick={transfer}>
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
