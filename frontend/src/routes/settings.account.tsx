import { createFileRoute } from "@tanstack/react-router";
import {
  FileX2Icon,
  KeyRoundIcon,
  MessageSquareXIcon,
  RotateCcwIcon,
  Trash2Icon,
  UserCogIcon,
} from "lucide-react";
import { useState } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../components/ui/alert-dialog";
import { Button } from "../components/ui/button";
import {
  deleteAllConversations,
  deleteAllDocuments,
  deleteAllUserData,
  revokeAllTokens,
} from "../lib/api";
import { useConversationsStore } from "../stores/conversations-store";
import { useSettingsStore } from "../stores/settings-store";
import { clearAllStorage } from "../stores/storage";
import { useUserDocumentsStore } from "../stores/user-documents-store";

export const Route = createFileRoute("/settings/account")({
  component: AccountPage,
});

// --- Danger Zone ---

type DangerAction = "conversations" | "documents" | "tokens" | "everything";

const DANGER_ACTIONS: Record<
  DangerAction,
  { title: string; description: string; confirm: string }
> = {
  conversations: {
    title: "Delete All Conversations",
    description:
      "This will permanently delete all your chat conversations on the server. This action cannot be undone.",
    confirm: "Delete All",
  },
  documents: {
    title: "Delete All Documents",
    description:
      "This will permanently delete all your documents, chunks, originals, and the search index on the server. This action cannot be undone.",
    confirm: "Delete All",
  },
  tokens: {
    title: "Revoke All Tokens",
    description:
      "This will permanently revoke all your personal access tokens. Any applications using these tokens will lose API access.",
    confirm: "Revoke All",
  },
  everything: {
    title: "Reset Everything",
    description:
      "This will permanently delete all your server-side data (conversations, documents, tokens) and clear all local browser data. This action cannot be undone.",
    confirm: "Reset Everything",
  },
};

function DangerZoneSection() {
  const [confirmAction, setConfirmAction] = useState<DangerAction | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const fetchConversations = useConversationsStore((s) => s.fetchConversations);
  const fetchDocuments = useUserDocumentsStore((s) => s.fetchDocuments);
  const fetchDirectoryTree = useUserDocumentsStore((s) => s.fetchDirectoryTree);

  const actionInfo = confirmAction ? DANGER_ACTIONS[confirmAction] : null;

  const handleConfirm = async () => {
    if (!confirmAction) return;
    setIsDeleting(true);
    try {
      switch (confirmAction) {
        case "conversations":
          await deleteAllConversations();
          await fetchConversations();
          break;
        case "documents":
          await deleteAllDocuments();
          await fetchDocuments();
          await fetchDirectoryTree();
          break;
        case "tokens":
          await revokeAllTokens();
          break;
        case "everything":
          await deleteAllUserData();
          clearAllStorage();
          return;
      }
    } catch (e) {
      console.error("Bulk delete failed:", e);
    } finally {
      setIsDeleting(false);
      setConfirmAction(null);
    }
  };

  return (
    <>
      <div className="grid gap-3">
        <h2 className="text-lg font-semibold text-destructive">
          Server — Danger Zone
        </h2>
        <p className="text-sm text-muted-foreground">
          These actions permanently delete data on the server.
        </p>
        <div className="grid grid-cols-2 gap-2">
          <Button
            variant="outline"
            size="sm"
            className="justify-start"
            onClick={() => setConfirmAction("conversations")}
          >
            <MessageSquareXIcon className="h-4 w-4 mr-2" />
            Delete All Chats
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="justify-start"
            onClick={() => setConfirmAction("documents")}
          >
            <FileX2Icon className="h-4 w-4 mr-2" />
            Delete All Documents
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="justify-start"
            onClick={() => setConfirmAction("tokens")}
          >
            <KeyRoundIcon className="h-4 w-4 mr-2" />
            Revoke All Tokens
          </Button>
          <Button
            variant="destructive"
            size="sm"
            className="justify-start"
            onClick={() => setConfirmAction("everything")}
          >
            <Trash2Icon className="h-4 w-4 mr-2" />
            Reset Everything
          </Button>
        </div>
      </div>

      <AlertDialog
        open={!!confirmAction}
        onOpenChange={(open) => !open && setConfirmAction(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{actionInfo?.title}</AlertDialogTitle>
            <AlertDialogDescription>
              {actionInfo?.description}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirm}
              disabled={isDeleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {isDeleting ? "Deleting..." : actionInfo?.confirm}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

// --- Main component ---

function AccountPage() {
  const reset = useSettingsStore((s) => s.reset);

  return (
    <div className="container max-w-4xl mx-auto py-8 px-4">
      <div className="flex items-center gap-3 mb-8">
        <UserCogIcon className="h-6 w-6" />
        <h1 className="text-2xl font-semibold">Account</h1>
      </div>

      <div className="grid gap-8">
        {/* Local Settings */}
        <div className="grid gap-3">
          <h2 className="text-lg font-semibold">Local Settings</h2>
          <p className="text-sm text-muted-foreground">
            Clears browser overrides and uses server-configured defaults.
          </p>
          <div>
            <Button variant="outline" size="sm" onClick={reset}>
              <RotateCcwIcon className="h-4 w-4 mr-2" />
              Reset to Server Defaults
            </Button>
          </div>
        </div>

        {/* Danger Zone */}
        <DangerZoneSection />
      </div>
    </div>
  );
}
