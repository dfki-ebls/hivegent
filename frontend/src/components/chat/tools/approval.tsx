import type { ComponentProps, ReactNode } from "react";
import { Trans, useTranslation } from "react-i18next";
import {
  type ChangesetSummary,
  ChangesetView,
  changeCount,
} from "@/components/chat/tools/changeset";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item";
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
  const { key, ...note } = round.noteField(id);

  const action = (approved: boolean): ComponentProps<typeof Button> => ({
    size: "sm",
    variant: (staging ? choice === approved : approved) ? "default" : "outline",
    "aria-pressed": staging ? choice === approved : undefined,
    disabled: !staging && round.blockedReason !== undefined,
    onClick: () => (staging ? round.stage(id, approved) : round.send(approved)),
  });

  return (
    <ItemFooter>
      <Input
        key={key}
        aria-label={t(($) => $.chat.tools.approval.note, { call: title })}
        placeholder={t(($) => $.chat.tools.approval.notePlaceholder)}
        {...note}
      />
      <ItemActions>
        <Button {...action(false)}>{t(($) => $.chat.tools.approval.deny)}</Button>
        <Button {...action(true)}>{t(($) => $.chat.tools.approval.approve)}</Button>
      </ItemActions>
    </ItemFooter>
  );
}

interface ApprovalPromptProps {
  icon: ReactNode;
  toolName: string;
  /** The call's title, naming it in the note's label. */
  title: string;
  approvalId: string;
  /** The workspace changes the call asks to make, in place of the generic question. */
  summary?: ChangesetSummary;
  /** What the call would run with. */
  children?: ReactNode;
}

interface PromptItemProps {
  icon: ReactNode;
  /** Names the prompt for screen readers. */
  label: string;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  /** The prompt's body below its title, spanning the whole width. */
  children: ReactNode;
}

/**
 * Something the agent asks the user, an approval or questions.
 * It asks rather than reports, so it stands outside the trace until it is answered.
 */
export function PromptItem({
  icon,
  label,
  title,
  description,
  actions,
  children,
}: PromptItemProps) {
  return (
    <Item variant="outline" render={<fieldset />} aria-label={label} className="min-w-0">
      <ItemMedia variant="icon">{icon}</ItemMedia>
      <ItemContent>
        <ItemTitle>{title}</ItemTitle>
        {description && <ItemDescription>{description}</ItemDescription>}
      </ItemContent>
      {actions && <ItemActions>{actions}</ItemActions>}
      {children}
    </Item>
  );
}

/**
 * A call that waits for the user's approval, with its input and the changes it would make.
 */
export function ApprovalPrompt({
  icon,
  toolName,
  title,
  approvalId,
  summary,
  children,
}: ApprovalPromptProps) {
  const { t } = useTranslation();
  const round = useApprovalRound();
  const answerable = round?.ids.includes(approvalId) ?? false;

  return (
    <PromptItem
      icon={icon}
      label={title}
      title={
        summary ? (
          t(($) => $.chat.tools.changeset.question, { count: changeCount(summary) })
        ) : (
          <Trans
            i18nKey={($) => $.chat.tools.approval.question}
            values={{ tool: toolDisplayName(t, toolName) }}
            components={{ bold: <strong /> }}
          />
        )
      }
      description={answerable && round?.ids.length === 1 ? round.blockedReason : undefined}
    >
      <div className="flex min-w-0 basis-full flex-col gap-3">
        {children}
        {summary && <ChangesetView summary={summary} />}
      </div>
      {round && answerable && <ApprovalControls id={approvalId} title={title} round={round} />}
    </PromptItem>
  );
}

/** The user's decision on a call once it is made, with the note they sent along. */
export function ApprovalDecision({ approval }: { approval: NonNullable<ToolPart["approval"]> }) {
  const { t } = useTranslation();

  return (
    <p className="whitespace-pre-wrap">
      <span className="font-medium text-foreground">
        {approval.approved
          ? t(($) => $.chat.tools.approval.approved)
          : t(($) => $.chat.tools.approval.denied)}
      </span>
      {approval.reason && (
        <>
          {" · "}
          {t(($) => $.chat.tools.approval.yourNote)} {approval.reason}
        </>
      )}
    </p>
  );
}
