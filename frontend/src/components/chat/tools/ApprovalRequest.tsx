import type { ToolUIPart } from "ai";
import type { ReactNode } from "react";
import { Trans, useTranslation } from "react-i18next";
import {
  Confirmation,
  ConfirmationAction,
  ConfirmationActions,
  ConfirmationRequest,
} from "@/components/ai-elements/confirmation";
import { useToolApproval } from "@/hooks/chat/use-tool-approval";
import { toolDisplayName } from "@/lib/chat/tool-part";

interface ApprovalRequestProps {
  toolName: string;
  approval: NonNullable<ToolUIPart["approval"]>;
  state: ToolUIPart["state"];
  /** What the call is about to do, in place of the generic question. */
  children?: ReactNode;
}

export function ApprovalRequest({ toolName, approval, state, children }: ApprovalRequestProps) {
  const { t } = useTranslation();
  const { decide, blockedReason } = useToolApproval();

  if (state !== "approval-requested" && approval.approved === undefined) return null;

  return (
    <Confirmation approval={approval} state={state}>
      <ConfirmationRequest>
        {children ?? (
          <span className="text-sm">
            <Trans
              i18nKey={($) => $.chat.tools.approval.question}
              values={{ tool: toolDisplayName(t, toolName) }}
              components={{ bold: <strong /> }}
            />
          </span>
        )}
      </ConfirmationRequest>
      {state !== "approval-requested" && approval.approved === true && (
        <span className="text-sm text-green-700 dark:text-green-400">
          {t(($) => $.chat.tools.approval.approved)}
        </span>
      )}
      {state !== "approval-requested" && approval.approved === false && (
        <span className="text-sm text-orange-700 dark:text-orange-400">
          {t(($) => $.chat.tools.approval.denied)}
        </span>
      )}
      <ConfirmationActions>
        {blockedReason && (
          <span className="mr-auto text-xs text-muted-foreground">{blockedReason}</span>
        )}
        <ConfirmationAction
          variant="outline"
          disabled={blockedReason !== undefined}
          onClick={() => decide(approval.id ?? "", false)}
        >
          {t(($) => $.chat.tools.approval.deny)}
        </ConfirmationAction>
        <ConfirmationAction
          disabled={blockedReason !== undefined}
          onClick={() => decide(approval.id ?? "", true)}
        >
          {t(($) => $.chat.tools.approval.approve)}
        </ConfirmationAction>
      </ConfirmationActions>
    </Confirmation>
  );
}
