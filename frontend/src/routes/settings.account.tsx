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
import { toast } from "sonner";
import { BackendReadyGate } from "../components/BackendReadyGate";
import { Button } from "../components/ui/button";
import { Label } from "../components/ui/label";
import { Switch } from "../components/ui/switch";
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
} from "../lib/api";
import { startImpersonation } from "../lib/impersonation";
import type { AdminGroupInfo, AdminUserInfo } from "../lib/types";
import { errorMessage } from "../lib/utils";
import { enforceLogin } from "../oidc";
import { useConversationsStore } from "../stores/conversations-store";
import { selectIsAdmin, selectUserId, useSettingsStore } from "../stores/settings-store";
import { clearAllStorage } from "../stores/storage";
import { useDocumentsStore } from "../stores/documents-store";

export const Route = createFileRoute("/settings/account")({
  beforeLoad: enforceLogin,
  component: () => (
    <BackendReadyGate>
      <AccountPage />
    </BackendReadyGate>
  ),
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
  run: () => Promise<void>;
}

interface ConfirmDialogProps {
  action: DangerAction | null;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

function ConfirmDialog({ action, busy, onConfirm, onCancel }: ConfirmDialogProps) {
  return (
    <AlertDialog open={!!action} onOpenChange={(open) => !open && onCancel()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{action?.title}</AlertDialogTitle>
          <AlertDialogDescription>{action?.description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            disabled={busy}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {busy ? "Working..." : action?.confirm}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// --- User Danger Zone ---

function UserDangerZoneSection({ setAction }: { setAction: (a: DangerAction) => void }) {
  const resetLocalSettings = useSettingsStore((s) => s.reset);
  const fetchConversations = useConversationsStore((s) => s.fetchConversations);
  const refreshDocuments = useDocumentsStore((s) => s.refresh);

  return (
    <div className="grid gap-3">
      <div className="flex items-center gap-2">
        <ShieldAlertIcon className="h-5 w-5 text-destructive" />
        <h2 className="text-lg font-semibold text-destructive">User — Danger Zone</h2>
      </div>
      <p className="text-sm text-muted-foreground">
        Destructive actions scoped to your account. These affect only you.
      </p>

      <div className="grid grid-cols-2 gap-2">
        <Button
          variant="outline"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "user-local",
              title: "Reset Local Settings",
              description:
                "Discard your browser-side overrides and fall back to the server-configured defaults. Server-side data is untouched.",
              confirm: "Reset Local Settings",
              run: async () => {
                resetLocalSettings();
              },
            })
          }
        >
          <RotateCcwIcon className="h-4 w-4 mr-2" />
          Reset Local Settings
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "user-chats",
              title: "Delete All Chats",
              description:
                "Permanently delete every chat you own on the server. This action cannot be undone.",
              confirm: "Delete All Chats",
              run: async () => {
                await deleteAllConversations();
                await fetchConversations();
              },
            })
          }
        >
          <MessageSquareXIcon className="h-4 w-4 mr-2" />
          Delete All Chats
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "user-docs",
              title: "Delete All Documents",
              description:
                "Permanently delete every document, chunk, original, and search-index entry you own. This action cannot be undone.",
              confirm: "Delete All Documents",
              run: async () => {
                await deleteAllDocuments(PERSONAL_SCOPE);
                await refreshDocuments(PERSONAL_SCOPE);
              },
            })
          }
        >
          <FileX2Icon className="h-4 w-4 mr-2" />
          Delete All Documents
        </Button>
        <Button
          variant="destructive"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "user-everything",
              title: "Reset Everything",
              description:
                "Permanently delete every server-side trace of your account (conversations, documents, tokens, memory) and clear all local browser data. This action cannot be undone.",
              confirm: "Reset Everything",
              run: async () => {
                await deleteAllUserData();
                clearAllStorage();
              },
            })
          }
        >
          <Trash2Icon className="h-4 w-4 mr-2" />
          Reset Everything
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
  return (
    <div className="grid gap-2 rounded-md border p-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        {icon}
        {label}
      </div>
      {loading ? (
        <p className="text-xs text-muted-foreground">Loading...</p>
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

const NO_USERS_LABEL = "No users have left a footprint yet.";

const userMeta = (u: AdminUserInfo) => `${u.document_count}d / ${u.conversation_count}c`;

// Fetches the admin overview once and feeds both admin sections.
function AdminSections({ setAction }: { setAction: (a: DangerAction) => void }) {
  const currentUserId = useSettingsStore(selectUserId);
  const [users, setUsers] = useState<AdminUserInfo[]>([]);
  const [groups, setGroups] = useState<AdminGroupInfo[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [u, g] = await Promise.all([adminListUsers(), adminListGroups()]);
      setUsers(u);
      setGroups(g);
    } catch (e) {
      console.error("Failed to load admin overview:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

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
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    adminGetMaintenance()
      .then(setEnabled)
      .catch((e: unknown) => {
        console.error("Failed to read maintenance mode:", e);
        toast.error("Failed to read maintenance mode", { description: errorMessage(e) });
      });
  }, []);

  const toggle = async (next: boolean) => {
    try {
      setEnabled(await adminSetMaintenance(next));
      toast.success(next ? "Maintenance mode enabled" : "Maintenance mode disabled");
    } catch (e) {
      toast.error("Failed to toggle maintenance mode", { description: errorMessage(e) });
    }
  };

  return (
    <div className="grid gap-3">
      <div className="flex items-center gap-2">
        <WrenchIcon className="h-5 w-5" />
        <h2 className="text-lg font-semibold">Admin — Maintenance</h2>
      </div>
      <div className="flex items-center justify-between gap-4 rounded-md border p-3">
        <div className="grid gap-1">
          <Label htmlFor="maintenance-mode">Maintenance mode</Label>
          <p className="text-sm text-muted-foreground">
            Lock out every non-admin user and show them a maintenance notice instead of the app.
            Admins keep full access. The setting is persisted and stays active across server
            restarts until an admin turns it off.
          </p>
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
  return (
    <div className="grid gap-3">
      <div className="flex items-center gap-2">
        <EyeIcon className="h-5 w-5" />
        <h2 className="text-lg font-semibold">Admin — Impersonation</h2>
      </div>
      <p className="text-sm text-muted-foreground">
        Browse the app as another user to reproduce reported issues. The session carries the
        privileges of the target user, never your admin powers, and a banner with an exit button
        stays visible at the top.
      </p>
      <AdminTargetList
        label="Impersonate a user"
        items={users}
        loading={loading}
        icon={<EyeIcon className="h-4 w-4" />}
        emptyLabel={NO_USERS_LABEL}
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
  return (
    <div className="grid gap-3">
      <div className="flex items-center gap-2">
        <ShieldAlertIcon className="h-5 w-5 text-destructive" />
        <h2 className="text-lg font-semibold text-destructive">Admin — Danger Zone</h2>
      </div>
      <p className="text-sm text-muted-foreground">
        Destructive actions scoped to the whole deployment. These affect every user.
      </p>

      <div className="grid grid-cols-2 gap-2">
        <Button
          variant="outline"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "admin-workspace",
              title: "Reset Workspace Files",
              description:
                "Wipe every workspace file on disk and the matching document rows in SQL — the two must stay in sync. Chunks (text + vector) cascade with the document rows. Conversations, tokens, memory, users, and groups are kept.",
              confirm: "Reset Workspace",
              run: async () => {
                await adminResetWorkspace();
                await refresh();
              },
            })
          }
        >
          <FolderXIcon className="h-4 w-4 mr-2" />
          Reset Workspace Files
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "admin-reindex",
              title: "Reindex Knowledge",
              description:
                "Reconcile every casebase: prune workspace and SQL orphans so disk and database stay in sync. Safe to run anytime; useful after manual file changes or an embedding configuration change.",
              confirm: "Reindex",
              run: async () => {
                await adminReindex();
              },
            })
          }
        >
          <RefreshCwIcon className="h-4 w-4 mr-2" />
          Reindex Knowledge
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "admin-database",
              title: "Reset Database",
              description:
                "Drop every user and group row along with everything that cascades: tokens, memory, conversations, documents, and chunks. Workspace files on disk survive.",
              confirm: "Reset Database",
              run: async () => {
                await adminResetDatabase();
                await refresh();
              },
            })
          }
        >
          <DatabaseZapIcon className="h-4 w-4 mr-2" />
          Reset Database
        </Button>
        <Button
          variant="destructive"
          size="sm"
          className="justify-start"
          onClick={() =>
            setAction({
              key: "admin-factory",
              title: "Factory Reset",
              description:
                "Wipe every workspace file on disk, every user and group, and every dependent row (documents, chunks, conversations, tokens, memory). Local browser data is cleared too. The deployment returns to the state of a fresh checkout. This action cannot be undone.",
              confirm: "Factory Reset",
              run: async () => {
                await adminFactoryReset();
                clearAllStorage();
              },
            })
          }
        >
          <FactoryIcon className="h-4 w-4 mr-2" />
          Factory Reset
        </Button>
      </div>

      <div className="grid md:grid-cols-2 gap-3 mt-2">
        <AdminTargetList
          label="Wipe one user's data"
          items={users}
          loading={loading}
          icon={<UserXIcon className="h-4 w-4 text-destructive" />}
          emptyLabel={NO_USERS_LABEL}
          renderMeta={userMeta}
          onSelect={(u) =>
            setAction({
              key: `admin-user-${u.id}`,
              title: `Wipe data for ${u.id}`,
              description: `Delete every document, chunk, original, conversation, token, and memory entry owned by user ${u.id}. ${u.document_count} document(s) and ${u.conversation_count} conversation(s) will be removed. This action cannot be undone.`,
              confirm: "Wipe User",
              run: async () => {
                await adminDeleteUserData(u.id);
                await refresh();
              },
            })
          }
        />
        <AdminTargetList
          label="Wipe one group's data"
          items={groups}
          loading={loading}
          icon={<UsersIcon className="h-4 w-4 text-destructive" />}
          emptyLabel="No groups are registered yet."
          renderMeta={(g) => `${g.document_count}d`}
          onSelect={(g) =>
            setAction({
              key: `admin-group-${g.id}`,
              title: `Wipe data for group ${g.id}`,
              description: `Delete every document, chunk, and original owned by group ${g.id}. ${g.document_count} document(s) will be removed. The group reappears the next time one of its members (per the OIDC token) uploads to it. This action cannot be undone.`,
              confirm: "Wipe Group",
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
  const isAdmin = useSettingsStore(selectIsAdmin);
  const [action, setAction] = useState<DangerAction | null>(null);
  const [busy, setBusy] = useState(false);

  const handleConfirm = async () => {
    if (!action) return;
    setBusy(true);
    try {
      await action.run();
      toast.success(`${action.title} — done`);
    } catch (e) {
      console.error(`${action.key} failed:`, e);
      toast.error(`${action.title} failed`, {
        description: errorMessage(e),
      });
    } finally {
      setBusy(false);
      setAction(null);
    }
  };

  return (
    <div className="container max-w-4xl mx-auto py-8 px-4">
      <div className="flex items-center gap-3 mb-8">
        <UserCogIcon className="h-6 w-6" />
        <h1 className="text-2xl font-semibold">Account</h1>
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
