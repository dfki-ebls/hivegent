import { createFileRoute } from "@tanstack/react-router";
import {
  DatabaseZapIcon,
  EyeIcon,
  FactoryIcon,
  FileX2Icon,
  FolderXIcon,
  MessageSquareXIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  ShieldAlertIcon,
  Trash2Icon,
  UserCogIcon,
  UserXIcon,
  UsersIcon,
  WrenchIcon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  adminDeleteGroupData,
  adminDeleteUserData,
  adminFactoryReset,
  adminGetMaintenance,
  adminListGroups,
  adminListUsers,
  PERSONAL_SCOPE,
  adminReindex,
  adminResetDatabase,
  adminResetWorkspace,
  adminSetMaintenance,
  deleteAllConversations,
  deleteAllDocuments,
  deleteAllUserData,
} from "@/lib/api";
import { i18n, keyPrefix } from "@/i18n";
import { startImpersonation } from "@/lib/impersonation";
import type { AdminGroupInfo, AdminUserInfo } from "@/lib/types";
import { errorMessage } from "@/lib/utils";
import { enforceLogin } from "@/oidc";
import { useConversationsStore } from "@/stores/conversations-store";
import { selectIsAdmin, selectUserId, useSettingsStore } from "@/stores/settings-store";
import { clearAllStorage } from "@/stores/storage";
import { useDocumentsStore } from "@/stores/documents-store";

const USER_DANGER_T = keyPrefix(($) => $.settings.account.userDanger);
const MAINTENANCE_T = keyPrefix(($) => $.settings.account.maintenance);
const ACCOUNT_T = keyPrefix(($) => $.settings.account);

export const Route = createFileRoute("/settings/account")({
  beforeLoad: enforceLogin,
  component: AccountPage,
});

// --- Generic confirm-action dialog ---
//
// Both the user-scoped and admin-scoped danger zones funnel their
// buttons through this single pending-action state machine.  Keeps the
// confirm flow consistent and the page free of one-off useState pairs.

interface DangerAction {
  key: string;
  title: string;
  description: string;
  confirm: string;
  /** Returns an optional detail line for the success toast (counts, outcomes). */
  run: () => Promise<string | void>;
}

interface ConfirmDialogProps {
  action: DangerAction | null;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

function ConfirmDialog({ action, busy, onConfirm, onCancel }: ConfirmDialogProps) {
  const { t } = useTranslation();

  return (
    <AlertDialog open={!!action} onOpenChange={(open) => !open && onCancel()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{action?.title}</AlertDialogTitle>
          <AlertDialogDescription>{action?.description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>{t(($) => $.common.actions.cancel)}</AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            disabled={busy}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {busy ? t(($) => $.common.states.working) : action?.confirm}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// --- User Danger Zone ---

function UserDangerZoneSection({ setAction }: { setAction: (a: DangerAction) => void }) {
  const { t } = useTranslation(undefined, USER_DANGER_T);
  const resetLocalSettings = useSettingsStore((s) => s.reset);
  const fetchConversations = useConversationsStore((s) => s.fetchConversations);
  const refreshDocuments = useDocumentsStore((s) => s.refresh);

  return (
    <div className="grid gap-3">
      <div className="flex items-center gap-2">
        <ShieldAlertIcon className="h-5 w-5 text-destructive" />
        <h2 className="text-lg font-semibold text-destructive">{t(($) => $.title)}</h2>
      </div>
      <p className="text-sm text-muted-foreground">{t(($) => $.description)}</p>

      <div className="grid grid-cols-2 gap-2">
        <Button
          variant="outline"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "user-local",
              title: t(($) => $.resetLocal.label),
              description: t(($) => $.resetLocal.description),
              confirm: t(($) => $.resetLocal.label),
              run: async () => {
                resetLocalSettings();
              },
            })
          }
        >
          <RotateCcwIcon className="h-4 w-4 mr-2" />
          {t(($) => $.resetLocal.label)}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "user-chats",
              title: t(($) => $.deleteChats.label),
              description: t(($) => $.deleteChats.description),
              confirm: t(($) => $.deleteChats.label),
              run: async () => {
                await deleteAllConversations();
                await fetchConversations();
              },
            })
          }
        >
          <MessageSquareXIcon className="h-4 w-4 mr-2" />
          {t(($) => $.deleteChats.label)}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "user-docs",
              title: t(($) => $.deleteDocuments.label),
              description: t(($) => $.deleteDocuments.description),
              confirm: t(($) => $.deleteDocuments.label),
              run: async () => {
                await deleteAllDocuments(PERSONAL_SCOPE);
                await refreshDocuments(PERSONAL_SCOPE);
              },
            })
          }
        >
          <FileX2Icon className="h-4 w-4 mr-2" />
          {t(($) => $.deleteDocuments.label)}
        </Button>
        <Button
          variant="destructive"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "user-everything",
              title: t(($) => $.resetEverything.label),
              description: t(($) => $.resetEverything.description),
              confirm: t(($) => $.resetEverything.label),
              run: async () => {
                await deleteAllUserData();
                clearAllStorage();
              },
            })
          }
        >
          <Trash2Icon className="h-4 w-4 mr-2" />
          {t(($) => $.resetEverything.label)}
        </Button>
      </div>
    </div>
  );
}

// --- Admin Danger Zone ---

interface AdminTargetSelectorProps<T extends { id: string }> {
  label: string;
  items: T[];
  loading: boolean;
  onSelect: (item: T) => void;
  renderLabel?: (item: T) => string;
  renderMeta: (item: T) => string;
  icon: React.ReactNode;
  emptyLabel: string;
}

function AdminTargetList<T extends { id: string }>({
  label,
  items,
  loading,
  onSelect,
  renderLabel = (item) => item.id,
  renderMeta,
  icon,
  emptyLabel,
}: AdminTargetSelectorProps<T>) {
  const { t } = useTranslation();

  return (
    <div className="grid gap-2 rounded-md border p-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        {icon}
        {label}
      </div>
      {loading ? (
        <p className="text-xs text-muted-foreground">{t(($) => $.common.states.loading)}</p>
      ) : items.length === 0 ? (
        <p className="text-xs text-muted-foreground">{emptyLabel}</p>
      ) : (
        <div className="grid gap-1.5 max-h-48 overflow-y-auto">
          {items.map((item) => (
            <Button
              key={item.id}
              variant="ghost"
              size="sm"
              className="justify-between font-normal text-xs h-auto py-1.5"
              onClick={() => onSelect(item)}
            >
              <span className="truncate">{renderLabel(item)}</span>
              <span className="text-muted-foreground shrink-0 ml-2">{renderMeta(item)}</span>
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}

const userMeta = (u: AdminUserInfo) =>
  i18n.t(($) => $.settings.account.userMeta, {
    documents: u.document_count,
    conversations: u.conversation_count,
  });

// Fetches the admin overview once and feeds both admin sections.
function AdminSections({ setAction }: { setAction: (a: DangerAction) => void }) {
  const currentUserId = useSettingsStore(selectUserId);
  const [users, setUsers] = useState<AdminUserInfo[]>([]);
  const [groups, setGroups] = useState<AdminGroupInfo[]>([]);
  const [loading, setLoading] = useState(true);

  // Only sets state once the requests settle, so the mount effect can call it.
  const load = useCallback(
    () =>
      Promise.all([adminListUsers(), adminListGroups()])
        .then(([u, g]) => {
          setUsers(u);
          setGroups(g);
        })
        .catch((e: unknown) => console.error("Failed to load admin overview:", e))
        .finally(() => setLoading(false)),
    [],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    await load();
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  // Self-targeting is never useful: an admin cannot impersonate themselves
  // and wiping their own account belongs in the user danger zone.
  const otherUsers = users.filter((u) => u.id !== currentUserId);

  return (
    <>
      <AdminMaintenanceSection />
      <AdminImpersonationSection users={otherUsers} loading={loading} />
      <UserDangerZoneSection setAction={setAction} />
      <AdminDangerZoneSection
        setAction={setAction}
        users={otherUsers}
        groups={groups}
        loading={loading}
        refresh={refresh}
      />
    </>
  );
}

// The switch stays disabled until the current server state is known, so
// it never shows a guessed value.
function AdminMaintenanceSection() {
  const { t } = useTranslation(undefined, MAINTENANCE_T);
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    adminGetMaintenance()
      .then(setEnabled)
      .catch((e: unknown) => {
        console.error("Failed to read maintenance mode:", e);
        toast.error(errorMessage(e));
      });
  }, []);

  const toggle = async (next: boolean) => {
    try {
      setEnabled(await adminSetMaintenance(next));
      toast.success(next ? t(($) => $.enabled) : t(($) => $.disabled));
    } catch (e) {
      toast.error(
        t(($) => $.toggleFailed),
        { description: errorMessage(e) },
      );
    }
  };

  return (
    <div className="grid gap-3">
      <div className="flex items-center gap-2">
        <WrenchIcon className="h-5 w-5" />
        <h2 className="text-lg font-semibold">{t(($) => $.title)}</h2>
      </div>
      <div className="flex items-center justify-between gap-4 rounded-md border p-3">
        <div className="grid gap-1">
          <Label htmlFor="maintenance-mode">{t(($) => $.label)}</Label>
          <p className="text-sm text-muted-foreground">{t(($) => $.description)}</p>
        </div>
        <Switch
          id="maintenance-mode"
          checked={enabled ?? false}
          disabled={enabled === null}
          onCheckedChange={(next) => void toggle(next)}
        />
      </div>
    </div>
  );
}

function AdminImpersonationSection({
  users,
  loading,
}: {
  users: AdminUserInfo[];
  loading: boolean;
}) {
  const { t } = useTranslation(undefined, ACCOUNT_T);

  return (
    <div className="grid gap-3">
      <div className="flex items-center gap-2">
        <EyeIcon className="h-5 w-5" />
        <h2 className="text-lg font-semibold">{t(($) => $.impersonation.title)}</h2>
      </div>
      <p className="text-sm text-muted-foreground">{t(($) => $.impersonation.description)}</p>
      <AdminTargetList
        label={t(($) => $.impersonation.list)}
        items={users}
        loading={loading}
        icon={<EyeIcon className="h-4 w-4" />}
        emptyLabel={t(($) => $.noUsers)}
        renderMeta={userMeta}
        onSelect={(u) => startImpersonation(u.id)}
      />
    </div>
  );
}

interface AdminDangerZoneProps {
  setAction: (a: DangerAction) => void;
  users: AdminUserInfo[];
  groups: AdminGroupInfo[];
  loading: boolean;
  refresh: () => Promise<void>;
}

function AdminDangerZoneSection({
  setAction,
  users,
  groups,
  loading,
  refresh,
}: AdminDangerZoneProps) {
  const { t } = useTranslation(undefined, ACCOUNT_T);

  return (
    <div className="grid gap-3">
      <div className="flex items-center gap-2">
        <ShieldAlertIcon className="h-5 w-5 text-destructive" />
        <h2 className="text-lg font-semibold text-destructive">{t(($) => $.adminDanger.title)}</h2>
      </div>
      <p className="text-sm text-muted-foreground">{t(($) => $.adminDanger.description)}</p>

      <div className="grid grid-cols-2 gap-2">
        <Button
          variant="outline"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "admin-workspace",
              title: t(($) => $.adminDanger.resetWorkspace.label),
              description: t(($) => $.adminDanger.resetWorkspace.description),
              confirm: t(($) => $.adminDanger.resetWorkspace.confirm),
              run: async () => {
                await adminResetWorkspace();
                await refresh();
              },
            })
          }
        >
          <FolderXIcon className="h-4 w-4 mr-2" />
          {t(($) => $.adminDanger.resetWorkspace.label)}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "admin-reindex",
              title: t(($) => $.adminDanger.reindex.label),
              description: t(($) => $.adminDanger.reindex.description),
              confirm: t(($) => $.adminDanger.reindex.confirm),
              run: async () => {
                const { message } = await adminReindex();
                await refresh();
                return message;
              },
            })
          }
        >
          <RefreshCwIcon className="h-4 w-4 mr-2" />
          {t(($) => $.adminDanger.reindex.label)}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "admin-database",
              title: t(($) => $.adminDanger.resetDatabase.label),
              description: t(($) => $.adminDanger.resetDatabase.description),
              confirm: t(($) => $.adminDanger.resetDatabase.label),
              run: async () => {
                await adminResetDatabase();
                await refresh();
              },
            })
          }
        >
          <DatabaseZapIcon className="h-4 w-4 mr-2" />
          {t(($) => $.adminDanger.resetDatabase.label)}
        </Button>
        <Button
          variant="destructive"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "admin-factory",
              title: t(($) => $.adminDanger.factoryReset.label),
              description: t(($) => $.adminDanger.factoryReset.description),
              confirm: t(($) => $.adminDanger.factoryReset.label),
              run: async () => {
                await adminFactoryReset();
                clearAllStorage();
              },
            })
          }
        >
          <FactoryIcon className="h-4 w-4 mr-2" />
          {t(($) => $.adminDanger.factoryReset.label)}
        </Button>
      </div>

      <div className="grid md:grid-cols-2 gap-3 mt-2">
        <AdminTargetList
          label={t(($) => $.adminDanger.wipeUser.list)}
          items={users}
          loading={loading}
          icon={<UserXIcon className="h-4 w-4 text-destructive" />}
          emptyLabel={t(($) => $.noUsers)}
          renderMeta={userMeta}
          onSelect={(u) =>
            setAction({
              key: `admin-user-${u.id}`,
              title: t(($) => $.adminDanger.wipeUser.title, { user: u.id }),
              description: t(($) => $.adminDanger.wipeUser.description, {
                user: u.id,
                documents: t(($) => $.documentCount, { count: u.document_count }),
                conversations: t(($) => $.conversationCount, { count: u.conversation_count }),
              }),
              confirm: t(($) => $.adminDanger.wipeUser.confirm),
              run: async () => {
                await adminDeleteUserData(u.id);
                await refresh();
              },
            })
          }
        />
        <AdminTargetList
          label={t(($) => $.adminDanger.wipeGroup.list)}
          items={groups}
          loading={loading}
          icon={<UsersIcon className="h-4 w-4 text-destructive" />}
          emptyLabel={t(($) => $.noGroups)}
          renderMeta={(g) => t(($) => $.groupMeta, { documents: g.document_count })}
          onSelect={(g) =>
            setAction({
              key: `admin-group-${g.id}`,
              title: t(($) => $.adminDanger.wipeGroup.title, { group: g.id }),
              description: t(($) => $.adminDanger.wipeGroup.description, {
                group: g.id,
                count: g.document_count,
              }),
              confirm: t(($) => $.adminDanger.wipeGroup.confirm),
              run: async () => {
                await adminDeleteGroupData(g.id);
                await refresh();
              },
            })
          }
        />
      </div>
    </div>
  );
}

// --- Main component ---

function AccountPage() {
  const { t } = useTranslation(undefined, ACCOUNT_T);
  const isAdmin = useSettingsStore(selectIsAdmin);
  const [action, setAction] = useState<DangerAction | null>(null);
  const [busy, setBusy] = useState(false);

  const handleConfirm = async () => {
    if (!action) return;
    setBusy(true);
    try {
      const detail = await action.run();
      toast.success(
        t(($) => $.done, { action: action.title }),
        detail ? { description: detail } : undefined,
      );
    } catch (e) {
      console.error(`${action.key} failed:`, e);
      toast.error(
        t(($) => $.failed, { action: action.title }),
        {
          description: errorMessage(e),
        },
      );
    } finally {
      setBusy(false);
      setAction(null);
    }
  };

  return (
    <div className="container max-w-4xl mx-auto py-8 px-4">
      <div className="flex items-center gap-3 mb-8">
        <UserCogIcon className="h-6 w-6" />
        <h1 className="text-2xl font-semibold">{t(($) => $.title)}</h1>
      </div>

      {/* Each section after the first is separated by a top divider, so the
          ordering can change without per-section border bookkeeping. */}
      <div className="grid gap-8 [&>*+*]:border-t [&>*+*]:pt-8">
        {isAdmin ? (
          <AdminSections setAction={setAction} />
        ) : (
          <UserDangerZoneSection setAction={setAction} />
        )}
      </div>

      <ConfirmDialog
        action={action}
        busy={busy}
        onConfirm={() => void handleConfirm()}
        onCancel={() => setAction(null)}
      />
    </div>
  );
}
