import { ListChecksIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item";
import { Textarea } from "@/components/ui/textarea";
import {
  ApprovalMetadataProvider,
  type ApprovalRound,
  ApprovalRoundProvider,
  useApprovalRoundState,
} from "@/hooks/chat/use-tool-approval";

/**
 * Answers the several pending requests of one message together, below their prompts.
 *
 * The prompts stage a choice and a note per call, so this only adds what spans
 * them: a shared note, answering all at once, and sending the staged choices.
 */
function ApprovalBar({ round }: { round: ApprovalRound }) {
  const { t } = useTranslation();
  const count = round.ids.length;

  if (count < 2) return null;

  const decided = round.ids.filter((id) => round.choices[id] !== undefined).length;
  const blocked = round.blockedReason !== undefined;
  const { key, ...note } = round.noteField();
  const title = t(($) => $.chat.tools.approval.pending, { count });

  return (
    <Item variant="muted" render={<section />} aria-label={title}>
      <ItemMedia variant="icon">
        <ListChecksIcon />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>{title}</ItemTitle>
        <ItemDescription aria-live="polite">
          {round.blockedReason ?? t(($) => $.chat.tools.approval.progress, { decided, count })}
        </ItemDescription>
      </ItemContent>
      <Textarea
        key={key}
        className="min-h-12 basis-full"
        aria-label={t(($) => $.chat.tools.approval.sharedNote)}
        placeholder={t(($) => $.chat.tools.approval.sharedNote)}
        {...note}
      />
      <ItemFooter className="justify-end">
        <ItemActions className="flex-wrap justify-end">
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
        </ItemActions>
      </ItemFooter>
    </Item>
  );
}

interface ApprovalScopeProps {
  /** The ids of the requests still open for an answer, in transcript order. */
  pending: readonly string[];
  /** What each gated call asked with, by tool call id. */
  metadata?: Readonly<Record<string, unknown>>;
  /** The message's parts, whose prompts and lines read the round and the metadata. */
  children: ReactNode;
}

/**
 * The approval state of one message around its parts.
 * Staging a choice re-renders only the scope and the prompts reading the round,
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
