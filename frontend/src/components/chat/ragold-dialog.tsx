import { useMutation } from "@tanstack/react-query";
import { SendIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { OptionSelect } from "@/components/option-select";
import { DialogBody, ScrollDialogContent } from "@/components/scroll-dialog-content";
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
import { Field, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import { ItemGroup } from "@/components/ui/item";
import { Spinner } from "@/components/common/spinner";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/ui/toast";
import { useChatContext, useConversationId } from "@/components/chat/chat-context";
import { i18n } from "@/i18n";
import { exportRagold } from "@/api/conversations";
import { useServerSettings } from "@/api/settings";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { errorMessage } from "@/lib/utils";
import { precedingUserText } from "@/lib/chat/chat-utils";
import {
  completeEntry,
  draftFromTurn,
  type RagoldDraft,
  type RagoldEntry,
  RagoldTransferError,
  sendToRagold,
  withDocument,
  withPassage,
} from "@/lib/ragold";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
import { T_OPTIONS, useOptions } from "@/components/chat/ragold/shared";
import { DocumentPicker } from "@/components/chat/ragold/document-picker";
import { PassageDialog, DocumentItem } from "@/components/chat/ragold/document-item";
import { KeyFactsField } from "@/components/chat/ragold/key-facts-field";

function transferErrorMessage(error: unknown): string {
  if (!(error instanceof RagoldTransferError)) return errorMessage(error);

  const message = i18n.t(($) => $.chat.ragold[error.reason]);

  return error.message ? `${message}: ${error.message}` : message;
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
  const { chat } = useChatContext();
  const workspaces = useWorkspaces();
  const [draft, setDraft] = useState(() => {
    const { documents, chunks } = useFetchedDocumentsStore.getState();

    return draftFromTurn(
      precedingUserText(chat.messages, messageId),
      reply,
      documents,
      chunks,
      workspaces,
    );
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
