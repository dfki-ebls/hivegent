import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { keyPrefix } from "@/i18n";
import { NameInputDialog } from "@/components/documents/NameInputDialog";

const T_OPTIONS = keyPrefix(($) => $.app.chatDebugger);

interface DebugChatDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Asks for the ID of any user's conversation and opens it in the chat debugger. */
export function DebugChatDialog({ open, onOpenChange }: DebugChatDialogProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const navigate = useNavigate();

  return (
    <NameInputDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t(($) => $.emptyTitle)}
      description={t(($) => $.emptyDescription)}
      label={t(($) => $.conversationId)}
      submitLabel={t(($) => $.open)}
      onSubmit={(id) => void navigate({ to: "/debug/chat", search: { id } })}
    />
  );
}
