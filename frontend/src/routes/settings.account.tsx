import { createFileRoute } from "@tanstack/react-router";
import { BrainIcon, EraserIcon, FileX2Icon, MessageSquareXIcon, Trash2Icon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { clearMemory, clearTmp, deleteAllUserData } from "@/api/account";
import { deleteAllConversations } from "@/api/conversations";
import { deleteAllDocuments, PERSONAL_SCOPE } from "@/api/documents";
import { PageHeader } from "@/components/page-header";
import { DangerZone } from "@/components/settings/danger-zone";
import { ExportSection } from "@/components/settings/export-section";
import { keyPrefix } from "@/i18n";
import { clearAllStorage } from "@/stores/storage";

const ACCOUNT_T = keyPrefix(($) => $.settings.account);

export const Route = createFileRoute("/settings/account")({ component: AccountPage });

/** The export of the user's data, and what the assistant keeps for the user and the user's own data, each cleared behind a confirmation. */
function AccountPage() {
  const { t } = useTranslation(undefined, ACCOUNT_T);

  return (
    <>
      <PageHeader title={t(($) => $.title)} description={t(($) => $.description)} />
      <ExportSection />
      <DangerZone
        title={t(($) => $.title)}
        description={t(($) => $.description)}
        destructive={false}
        entries={[
          {
            key: "memory",
            icon: BrainIcon,
            title: t(($) => $.clearMemory.label),
            description: t(($) => $.clearMemory.description),
            confirm: t(($) => $.clear),
            run: clearMemory,
          },
          {
            key: "tmp",
            icon: EraserIcon,
            title: t(($) => $.clearTmp.label),
            description: t(($) => $.clearTmp.description),
            confirm: t(($) => $.clear),
            // The temporary files live outside the document tree, so the
            // count is the only evidence the press did anything.
            run: async () => t(($) => $.clearTmp.done, { count: (await clearTmp()).files_removed }),
          },
        ]}
      />
      <DangerZone
        title={t(($) => $.danger.title)}
        description={t(($) => $.danger.description)}
        entries={[
          {
            key: "chats",
            icon: MessageSquareXIcon,
            title: t(($) => $.danger.deleteChats.label),
            description: t(($) => $.danger.deleteChats.description),
            run: deleteAllConversations,
          },
          {
            key: "documents",
            icon: FileX2Icon,
            title: t(($) => $.danger.deleteDocuments.label),
            description: t(($) => $.danger.deleteDocuments.description),
            run: () => deleteAllDocuments(PERSONAL_SCOPE),
          },
          {
            key: "everything",
            icon: Trash2Icon,
            severe: true,
            title: t(($) => $.danger.resetEverything.label),
            description: t(($) => $.danger.resetEverything.description),
            run: async () => {
              await deleteAllUserData();
              clearAllStorage();
            },
          },
        ]}
      />
    </>
  );
}
