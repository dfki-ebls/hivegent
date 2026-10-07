import type { ComponentProps } from "react";
import { Trans, useTranslation } from "react-i18next";
import {
  Confirmation,
  ConfirmationAction,
  ConfirmationActions,
  ConfirmationRequest,
} from "@/components/ai-elements/confirmation";
import {
  type ChangesetSummary,
  ChangesetView,
  changeCount,
} from "@/components/chat/tools/changeset";
import { Input } from "@/components/ui/input";
import { type ApprovalRound, useApprovalRound } from "@/hooks/chat/use-tool-approval";
import { type ToolPart, toolDisplayName } from "@/lib/chat/tool-part";

interface ApprovalControlsProps {
  id: string;
  title: string;
  round: ApprovalRound;
}

/**
 * The note and the buttons of one request.
 * A lone request is sent on click, while one of several only stages the choice
 * for the message's `ApprovalBar` to send, so only sending waits for the gate.
 */
function ApprovalControls({ id, title, round }: ApprovalControlsProps) {
  const { t } = useTranslation();
  const staging = round.ids.length > 1;
  const choice = round.choices[id];
  const blockedReason = staging ? undefined : round.blockedReason;
  const { key, ...note } = round.noteField(id);

  const action = (approved: boolean): ComponentProps<typeof ConfirmationAction> => ({
    variant: (staging ? choice === approved : approved) ? "default" : "outline",
    "aria-pressed": staging ? choice === approved : undefined,
    disabled: blockedReason !== undefined,
    onClick: () => (staging ? round.stage(id, approved) : round.send(approved)),
  });

  return (
    <>
      <Input
        key={key}
        className="h-8"
        aria-label={t(($) => $.chat.tools.approval.note, { call: title })}
        placeholder={t(($) => $.chat.tools.approval.notePlaceholder)}
        {...note}
      />
      <ConfirmationActions>
        {blockedReason && (
          <span className="mr-auto text-xs text-muted-foreground">{blockedReason}</span>
        )}
        <ConfirmationAction {...action(false)}>
          {t(($) => $.chat.tools.approval.deny)}
        </ConfirmationAction>
        <ConfirmationAction {...action(true)}>
          {t(($) => $.chat.tools.approval.approve)}
        </ConfirmationAction>
      </ConfirmationActions>
    </>
  );
}

interface ApprovalRequestProps {
  toolName: string;
  /** The card's title, naming the call in the note's label. */
  title: string;
  approval: NonNullable<ToolPart["approval"]>;
  state: ToolPart["state"];
  /** The workspace changes the call asks to make, in place of the generic question. */
  summary?: ChangesetSummary;
}

/** What a gated call asks for and how to answer it, or the decision once made. */
export function ApprovalRequest({ toolName, title, approval, state, summary }: ApprovalRequestProps) {
  const { t } = useTranslation();
  const round = useApprovalRound();
  const pending = state === "approval-requested";

  if (!pending && approval.approved === undefined) return null;

  return (
    <Confirmation approval={approval} state={state}>
      <ConfirmationRequest>
        <p className="text-sm">
          {summary ? (
            t(($) => $.chat.tools.changeset.question, { count: changeCount(summary) })
          ) : (
            <Trans
              i18nKey={($) => $.chat.tools.approval.question}
              values={{ tool: toolDisplayName(t, toolName) }}
              components={{ bold: <strong /> }}
            />
          )}
        </p>
      </ConfirmationRequest>
      {/* Outside the request, so the changes stay visible once the call is resolved. */}
      {summary && <ChangesetView summary={summary} />}
      {round?.ids.includes(approval.id) && (
        <ApprovalControls id={approval.id} title={title} round={round} />
      )}
      {!pending && approval.approved === true && (
        <span className="text-sm text-green-700 dark:text-green-400">
          {t(($) => $.chat.tools.approval.approved)}
        </span>
      )}
      {!pending && approval.approved === false && (
        <span className="text-sm text-orange-700 dark:text-orange-400">
          {t(($) => $.chat.tools.approval.denied)}
        </span>
      )}
      {!pending && approval.reason && (
        <p className="text-sm whitespace-pre-wrap">
          <span className="text-muted-foreground">{t(($) => $.chat.tools.approval.yourNote)} </span>
          {approval.reason}
        </p>
      )}
    </Confirmation>
  );
}
