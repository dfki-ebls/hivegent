import type { ToolUIPart } from "ai";
import {
  Confirmation,
  ConfirmationAccepted,
  ConfirmationAction,
  ConfirmationActions,
  ConfirmationRejected,
  ConfirmationRequest,
} from "@/components/ai-elements/confirmation";
import { snakeCaseToTitleCase } from "@/lib/utils";

interface ApprovalRequestProps {
  toolName: string;
  approval: NonNullable<ToolUIPart["approval"]>;
  state: ToolUIPart["state"];
  onApprove: (id: string) => void;
  onDeny: (id: string) => void;
}

export function ApprovalRequest({
  toolName,
  approval,
  state,
  onApprove,
  onDeny,
}: ApprovalRequestProps) {
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
        <ConfirmationAction variant="outline" onClick={() => onDeny(approval.id ?? "")}>
          Deny
        </ConfirmationAction>
        <ConfirmationAction onClick={() => onApprove(approval.id ?? "")}>
          Approve
        </ConfirmationAction>
      </ConfirmationActions>
    </Confirmation>
  );
}
