import { useTranslation } from "react-i18next";

import { useServerSettings } from "@/api/settings";
import type { Workspace } from "@/lib/document-tree";
import { useUserName } from "@/hooks/use-user-name";

/**
 * The workspaces the user can read, in the server's scope order (personal,
 * groups, global), each with the grant the server derived for it.
 * Subscribed, not snapshotted, since they arrive with the settings fetch
 * after the first render.
 */
export function useWorkspaces(): Workspace[] {
  const { t } = useTranslation();
  const userName = useUserName();
  const workspaces = useServerSettings((s) => s.user.workspaces);
  const labels = {
    global: t(($) => $.documents.manager.globalWorkspace),
    user: userName,
  };

  return (workspaces ?? []).map(({ scope, kind, name, writable }) => ({
    scope,
    kind,
    label: kind === "group" || kind === "source" ? name : labels[kind],
    writable,
  }));
}
