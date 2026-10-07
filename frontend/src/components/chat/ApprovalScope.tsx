import { type ReactNode, useId } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  ApprovalMetadataProvider,
  type ApprovalRound,
  ApprovalRoundProvider,
  useApprovalRoundState,
} from "@/hooks/chat/use-tool-approval";

/**
 * Answers the several pending requests of one message together, below their cards.
 *
 * The cards stage a choice and a note per call, so this only adds what spans
 * them: a shared note, answering all at once, and sending the staged choices.
 */
function ApprovalBar({ round }: { round: ApprovalRound }) {
  const { t } = useTranslation();
  const headingId = useId();
  const count = round.ids.length;

  if (count < 2) return null;

  const decided = round.ids.filter((id) => round.choices[id] !== undefined).length;
  const blocked = round.blockedReason !== undefined;

  return (
    <section
      aria-labelledby={headingId}
      className="space-y-2 rounded-md border bg-muted/40 p-3 text-sm"
    >
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 id={headingId} className="mr-auto font-medium">
          {t(($) => $.chat.tools.approval.pending, { count })}
        </h3>
        {round.blockedReason && (
          <span className="text-xs text-muted-foreground">{round.blockedReason}</span>
        )}
      </div>
      <Textarea
        className="min-h-12"
        aria-label={t(($) => $.chat.tools.approval.sharedNote)}
        placeholder={t(($) => $.chat.tools.approval.sharedNote)}
        {...round.noteField()}
      />
      <div className="flex flex-wrap items-center justify-end gap-2">
        <span className="mr-auto text-xs text-muted-foreground" aria-live="polite">
          {t(($) => $.chat.tools.approval.progress, { decided, count })}
        </span>
        <Button
          size="sm"
          variant="secondary"
          disabled={blocked || decided < count}
          onClick={() => round.send()}
        >
          {t(($) => $.chat.tools.approval.submit)}
        </Button>
        <Button size="sm" variant="outline" disabled={blocked} onClick={() => round.send(false)}>
          {t(($) => $.chat.tools.approval.denyAll)}
        </Button>
        <Button size="sm" disabled={blocked} onClick={() => round.send(true)}>
          {t(($) => $.chat.tools.approval.approveAll)}
        </Button>
      </div>
    </section>
  );
}

interface ApprovalScopeProps {
  /** The ids of the requests still open for an answer, in transcript order. */
  pending: readonly string[];
  /** What each gated call asked with, by tool call id. */
  metadata?: Readonly<Record<string, unknown>>;
  /** The message's parts, whose cards read the round and the metadata. */
  children: ReactNode;
}

/**
 * The approval state of one message around its parts.
 * Staging a choice re-renders only the scope and the cards reading the round,
 * since the parts arrive as elements the message already built.
 */
export function ApprovalScope({ pending, metadata, children }: ApprovalScopeProps) {
  const round = useApprovalRoundState(pending);

  return (
    <>
      <ApprovalMetadataProvider value={metadata}>
        <ApprovalRoundProvider value={round}>{children}</ApprovalRoundProvider>
      </ApprovalMetadataProvider>
      <ApprovalBar round={round} />
    </>
  );
}
