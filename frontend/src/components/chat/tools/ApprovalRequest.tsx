import type { ToolUIPart } from "ai";
import {
  Confirmation,
  ConfirmationAccepted,
  ConfirmationAction,
  ConfirmationActions,
  ConfirmationRejected,
  ConfirmationRequest,
} from "@/components/ai-elements/confirmation";
import { useToolApproval } from "@/hooks/chat/use-tool-approval";
import { snakeCaseToTitleCase } from "@/lib/utils";

interface ApprovalRequestProps {
  toolName: string;
  approval: NonNullable<ToolUIPart["approval"]>;
  state: ToolUIPart["state"];
}

export function ApprovalRequest({ toolName, approval, state }: ApprovalRequestProps) {
  const { decide, blockedReason } = useToolApproval();

  return (
    <Confirmation approval={approval} state={state}>
      <ConfirmationRequest>
        <span className="text-sm">
          Allow the assistant to run <strong>{snakeCaseToTitleCase(toolName)}</strong>?
        </span>
      </ConfirmationRequest>
      <ConfirmationAccepted>
        <span className="text-sm text-green-700 dark:text-green-400">Approved</span>
      </ConfirmationAccepted>
      <ConfirmationRejected>
        <span className="text-sm text-orange-700 dark:text-orange-400">Denied</span>
      </ConfirmationRejected>
      <ConfirmationActions>
        {blockedReason && (
          <span className="mr-auto text-xs text-muted-foreground">{blockedReason}</span>
        )}
        <ConfirmationAction
          variant="outline"
          disabled={blockedReason !== undefined}
          onClick={() => decide(approval.id ?? "", false)}
        >
          Deny
        </ConfirmationAction>
        <ConfirmationAction
          disabled={blockedReason !== undefined}
          onClick={() => decide(approval.id ?? "", true)}
        >
          Approve
        </ConfirmationAction>
      </ConfirmationActions>
    </Confirmation>
  );
}
