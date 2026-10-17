import { useMutation, useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  DatabaseZapIcon,
  EyeIcon,
  FactoryIcon,
  FileX2Icon,
  FolderXIcon,
  type LucideIcon,
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
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { TooltipButton } from "@/components/TooltipButton";
import { Button } from "@/components/ui/button";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSeparator,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import { useDialogTarget } from "@/hooks/use-dialog-target";
import { keyPrefix } from "@/i18n";
import {
  adminDeleteGroupData,
  adminDeleteUserData,
  adminReindex,
  adminReset,
  deleteAllConversations,
  deleteAllDocuments,
  deleteAllUserData,
  PERSONAL_SCOPE,
} from "@/lib/api";
import { startImpersonation } from "@/lib/impersonation";
import type { AdminGroupInfo, AdminUserInfo } from "@/lib/types";
import { cn, errorMessage } from "@/lib/utils";
import { enforceLogin } from "@/oidc";
import { adminMutations, adminQueries } from "@/queries/admin";
import { selectUserId, useIsAdmin, useServerSettings } from "@/queries/settings";
import { useSettingsStore } from "@/stores/settings-store";
import { clearAllStorage } from "@/stores/storage";

const USER_DANGER_T = keyPrefix(($) => $.settings.account.userDanger);
const MAINTENANCE_T = keyPrefix(($) => $.settings.account.maintenance);
const ACCOUNT_T = keyPrefix(($) => $.settings.account);

export const Route = createFileRoute("/settings/account")({
  beforeLoad: enforceLogin,
  component: AccountPage,
});

/**
 * An action behind the page's one confirmation dialog, so every danger zone
 * shares the same confirm flow.
 */
interface DangerAction {
  key: string;
  title: string;
  description: string;
  /** The label of the confirm button, the title by default. */
  confirm?: string;
  /** Returns an optional detail line for the success toast (counts, outcomes). */
  run: () => Promise<string | void>;
}

/** A danger zone row, whose button opens the confirmation of its action. */
interface DangerEntry extends DangerAction {
  icon: LucideIcon;
  /** Marks the most destructive entry of a zone. */
  severe?: boolean;
}

type SelectAction = (action: DangerAction) => void;

interface SectionProps {
  icon: LucideIcon;
  title: string;
  description?: string;
  destructive?: boolean;
  children: ReactNode;
}

function Section({ icon: Icon, title, description, destructive, children }: SectionProps) {
  return (
    <FieldSet>
      <FieldLegend className={cn("flex items-center gap-2", destructive && "text-destructive")}>
        <Icon className="size-4" />
        {title}
      </FieldLegend>
      {description && <FieldDescription>{description}</FieldDescription>}
      {children}
    </FieldSet>
  );
}

/** A danger zone: every entry describes its action next to the button confirming it. */
function DangerZone({
  title,
  description,
  entries,
  onSelect,
  children,
}: {
  title: string;
  description: string;
  entries: DangerEntry[];
  onSelect: SelectAction;
  children?: ReactNode;
}) {
  return (
    <Section icon={ShieldAlertIcon} title={title} description={description} destructive>
      <ItemGroup>
        {entries.map(({ icon: Icon, severe, ...action }) => (
          <Item key={action.key} variant="outline" size="sm">
            <ItemContent>
              <ItemTitle>{action.title}</ItemTitle>
              <ItemDescription>{action.description}</ItemDescription>
            </ItemContent>
            <ItemActions>
              <Button
                variant={severe ? "destructive" : "outline"}
                size="sm"
                onClick={() => onSelect(action)}
              >
                <Icon data-icon="inline-start" />
                {action.confirm ?? action.title}
              </Button>
            </ItemActions>
          </Item>
        ))}
      </ItemGroup>
      {children}
    </Section>
  );
}

function UserDangerZone({ onSelect }: { onSelect: SelectAction }) {
  const { t } = useTranslation(undefined, USER_DANGER_T);
  const resetLocalSettings = useSettingsStore((s) => s.reset);

  return (
    <DangerZone
      title={t(($) => $.title)}
      description={t(($) => $.description)}
      onSelect={onSelect}
      entries={[
        {
          key: "user-local",
          icon: RotateCcwIcon,
          title: t(($) => $.resetLocal.label),
          description: t(($) => $.resetLocal.description),
          run: async () => {
            resetLocalSettings();
          },
        },
        {
          key: "user-chats",
          icon: MessageSquareXIcon,
          title: t(($) => $.deleteChats.label),
          description: t(($) => $.deleteChats.description),
          run: deleteAllConversations,
        },
        {
          key: "user-docs",
          icon: FileX2Icon,
          title: t(($) => $.deleteDocuments.label),
          description: t(($) => $.deleteDocuments.description),
          run: () => deleteAllDocuments(PERSONAL_SCOPE),
        },
        {
          key: "user-everything",
          icon: Trash2Icon,
          severe: true,
          title: t(($) => $.resetEverything.label),
          description: t(($) => $.resetEverything.description),
          run: async () => {
            await deleteAllUserData();
            clearAllStorage();
          },
        },
      ]}
    />
  );
}

interface TargetListProps<T extends { id: string }> {
  label: string;
  items: T[];
  loading: boolean;
  emptyLabel: string;
  meta: (item: T) => string;
  actionLabel: string;
  actionIcon: LucideIcon;
  destructive?: boolean;
  onSelect: (item: T) => void;
}

/** The users or groups an admin action can target, each with the button applying it. */
function TargetList<T extends { id: string }>({
  label,
  items,
  loading,
  emptyLabel,
  meta,
  actionLabel,
  actionIcon: Icon,
  destructive,
  onSelect,
}: TargetListProps<T>) {
  const { t } = useTranslation();

  return (
    <FieldSet className="gap-3">
      <FieldLegend variant="label">{label}</FieldLegend>
      {loading ? (
        <Spinner aria-label={t(($) => $.common.states.loading)} />
      ) : items.length === 0 ? (
        <FieldDescription>{emptyLabel}</FieldDescription>
      ) : (
        <ItemGroup className="max-h-64 overflow-y-auto">
          {items.map((item) => (
            <Item key={item.id} variant="outline" size="xs">
              <ItemContent className="min-w-0">
                <ItemTitle className="break-all">{item.id}</ItemTitle>
                <ItemDescription>{meta(item)}</ItemDescription>
              </ItemContent>
              <ItemActions>
                <TooltipButton
                  label={actionLabel}
                  variant={destructive ? "destructive" : "ghost"}
                  onClick={() => onSelect(item)}
                >
                  <Icon />
                </TooltipButton>
              </ItemActions>
            </Item>
          ))}
        </ItemGroup>
      )}
    </FieldSet>
  );
}

/** The document and conversation counts of a user. */
function useUserMeta(): (user: AdminUserInfo) => string {
  const { t } = useTranslation(undefined, ACCOUNT_T);

  return (user) =>
    t(($) => $.userMeta, {
      documents: t(($) => $.documentCount, { count: user.document_count }),
      conversations: t(($) => $.conversationCount, { count: user.conversation_count }),
    });
}

/** Fetches the admin overview and feeds every admin section. */
function AdminSections({ onSelect }: { onSelect: SelectAction }) {
  const currentUserId = useServerSettings(selectUserId);
  const { data, isPending: loading } = useQuery(adminQueries.overview());

  // Self-targeting is never useful: an admin cannot impersonate themselves
  // and wiping their own account belongs in the user danger zone.
  const otherUsers = data?.users.filter((u) => u.id !== currentUserId) ?? [];

  return (
    <>
      <MaintenanceSection />
      <FieldSeparator />
      <ImpersonationSection users={otherUsers} loading={loading} />
      <FieldSeparator />
      <UserDangerZone onSelect={onSelect} />
      <FieldSeparator />
      <AdminDangerZone
        onSelect={onSelect}
        users={otherUsers}
        groups={data?.groups ?? []}
        loading={loading}
      />
    </>
  );
}

// The switch stays disabled until the current server state is known, so
// it never shows a guessed value.
function MaintenanceSection() {
  const { t } = useTranslation(undefined, MAINTENANCE_T);
  const { data: enabled } = useQuery(adminQueries.maintenance());
  const toggle = useMutation({
    ...adminMutations.maintenance(),
    onSuccess: (next) => {
      toast.add({ type: "success", title: next ? t(($) => $.enabled) : t(($) => $.disabled) });
    },
    onError: (e) =>
      toast.add({ type: "error", title: t(($) => $.toggleFailed), description: errorMessage(e) }),
  });

  return (
    <Section icon={WrenchIcon} title={t(($) => $.title)}>
      <FieldLabel htmlFor="maintenance-mode">
        <Field orientation="horizontal" data-disabled={enabled === undefined}>
          <FieldContent>
            <FieldTitle>{t(($) => $.label)}</FieldTitle>
            <FieldDescription>{t(($) => $.description)}</FieldDescription>
          </FieldContent>
          <Switch
            id="maintenance-mode"
            checked={enabled ?? false}
            disabled={enabled === undefined}
            onCheckedChange={(next) => toggle.mutate(next)}
          />
        </Field>
      </FieldLabel>
    </Section>
  );
}

function ImpersonationSection({ users, loading }: { users: AdminUserInfo[]; loading: boolean }) {
  const { t } = useTranslation(undefined, ACCOUNT_T);
  const userMeta = useUserMeta();

  return (
    <Section
      icon={EyeIcon}
      title={t(($) => $.impersonation.title)}
      description={t(($) => $.impersonation.description)}
    >
      <TargetList
        label={t(($) => $.impersonation.list)}
        items={users}
        loading={loading}
        emptyLabel={t(($) => $.noUsers)}
        meta={userMeta}
        actionLabel={t(($) => $.impersonation.start)}
        actionIcon={EyeIcon}
        onSelect={(u) => startImpersonation(u.id)}
      />
    </Section>
  );
}

interface AdminDangerZoneProps {
  onSelect: SelectAction;
  users: AdminUserInfo[];
  groups: AdminGroupInfo[];
  loading: boolean;
}

function AdminDangerZone({ onSelect, users, groups, loading }: AdminDangerZoneProps) {
  const { t } = useTranslation(undefined, ACCOUNT_T);
  const userMeta = useUserMeta();

  return (
    <DangerZone
      title={t(($) => $.adminDanger.title)}
      description={t(($) => $.adminDanger.description)}
      onSelect={onSelect}
      entries={[
        {
          key: "admin-workspace",
          icon: FolderXIcon,
          title: t(($) => $.adminDanger.resetWorkspace.label),
          description: t(($) => $.adminDanger.resetWorkspace.description),
          confirm: t(($) => $.adminDanger.resetWorkspace.confirm),
          run: () => adminReset("workspace"),
        },
        {
          key: "admin-reindex",
          icon: RefreshCwIcon,
          title: t(($) => $.adminDanger.reindex.label),
          description: t(($) => $.adminDanger.reindex.description),
          confirm: t(($) => $.adminDanger.reindex.confirm),
          run: async () => (await adminReindex()).message,
        },
        {
          key: "admin-database",
          icon: DatabaseZapIcon,
          title: t(($) => $.adminDanger.resetDatabase.label),
          description: t(($) => $.adminDanger.resetDatabase.description),
          run: () => adminReset("database"),
        },
        {
          key: "admin-factory",
          icon: FactoryIcon,
          severe: true,
          title: t(($) => $.adminDanger.factoryReset.label),
          description: t(($) => $.adminDanger.factoryReset.description),
          run: async () => {
            await adminReset("factory");
            clearAllStorage();
          },
        },
      ]}
    >
      <div className="grid gap-6 md:grid-cols-2">
        <TargetList
          label={t(($) => $.adminDanger.wipeUser.list)}
          items={users}
          loading={loading}
          emptyLabel={t(($) => $.noUsers)}
          meta={userMeta}
          actionLabel={t(($) => $.adminDanger.wipeUser.confirm)}
          actionIcon={UserXIcon}
          destructive
          onSelect={(u) =>
            onSelect({
              key: `admin-user-${u.id}`,
              title: t(($) => $.adminDanger.wipeUser.title, { user: u.id }),
              description: t(($) => $.adminDanger.wipeUser.description, {
                user: u.id,
                documents: t(($) => $.documentCount, { count: u.document_count }),
                conversations: t(($) => $.conversationCount, { count: u.conversation_count }),
              }),
              confirm: t(($) => $.adminDanger.wipeUser.confirm),
              run: () => adminDeleteUserData(u.id),
            })
          }
        />
        <TargetList
          label={t(($) => $.adminDanger.wipeGroup.list)}
          items={groups}
          loading={loading}
          emptyLabel={t(($) => $.noGroups)}
          meta={(g) => t(($) => $.documentCount, { count: g.document_count })}
          actionLabel={t(($) => $.adminDanger.wipeGroup.confirm)}
          actionIcon={UsersIcon}
          destructive
          onSelect={(g) =>
            onSelect({
              key: `admin-group-${g.id}`,
              title: t(($) => $.adminDanger.wipeGroup.title, { group: g.id }),
              description: t(($) => $.adminDanger.wipeGroup.description, {
                group: g.id,
                count: g.document_count,
              }),
              confirm: t(($) => $.adminDanger.wipeGroup.confirm),
              run: () => adminDeleteGroupData(g.id),
            })
          }
        />
      </div>
    </DangerZone>
  );
}

function AccountPage() {
  const { t } = useTranslation(undefined, ACCOUNT_T);
  const isAdmin = useIsAdmin();
  const { target: action, open, show, setOpen } = useDialogTarget<DangerAction | null>(null);
  const confirm = useMutation({
    mutationFn: (target: DangerAction) => target.run(),
    // Any action here can change what every cached view shows.
    meta: { invalidates: "all" },
    onSuccess: (detail, target) => {
      toast.add({
        type: "success",
        title: t(($) => $.done, { action: target.title }),
        description: detail || undefined,
      });
    },
    onError: (e, target) => {
      console.error(`${target.key} failed:`, e);
      toast.add({
        type: "error",
        title: t(($) => $.failed, { action: target.title }),
        description: errorMessage(e),
      });
    },
    onSettled: () => setOpen(false),
  });

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-8 px-4 py-8">
      <h1 className="flex items-center gap-3 text-2xl font-semibold">
        <UserCogIcon className="size-6" />
        {t(($) => $.title)}
      </h1>

      <FieldGroup>
        {isAdmin ? <AdminSections onSelect={show} /> : <UserDangerZone onSelect={show} />}
      </FieldGroup>

      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={action?.title ?? ""}
        description={action?.description ?? ""}
        confirmLabel={action?.confirm ?? action?.title ?? ""}
        busy={confirm.isPending}
        onConfirm={() => action && confirm.mutate(action)}
      />
    </div>
  );
}
