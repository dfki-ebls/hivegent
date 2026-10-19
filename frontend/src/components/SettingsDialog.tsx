import { useMutation, useQuery } from "@tanstack/react-query";
import {
  CheckIcon,
  EraserIcon,
  LockIcon,
  type LucideIcon,
  PlugIcon,
  PlusIcon,
  RotateCcwIcon,
  SettingsIcon,
  TrashIcon,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import { i18n, isCatalogKey, keyPrefix } from "@/i18n";
import { settings } from "@/i18n/locales/en/settings";
import { clearMemory, clearTmp, testMcpServer } from "@/lib/api";
import { featureFlags } from "@/lib/feature-flags";
import {
  type McpOAuth2Config,
  type McpServerEntry,
  PersonalitySchema,
  type UserOverrides,
} from "@/lib/types";
import { cn, errorMessage } from "@/lib/utils";
import { toolQueries } from "@/queries/tools";
import { useServerSettings } from "@/queries/settings";
import { useSettingsStore } from "@/stores/settings-store";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { OptionSelect } from "@/components/OptionSelect";
import { DialogBody, ScrollDialogContent } from "@/components/ScrollDialogContent";
import { TooltipButton, WithTooltip } from "@/components/TooltipButton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { InputGroupButton } from "@/components/ui/input-group";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/ui/toast";

const DIALOG_T = keyPrefix(($) => $.settings.dialog);
const OPTIONS_T = keyPrefix(($) => $.options);

/** The column classes by the count of the enabled columns, spelled out for Tailwind to see. */
const GRID_COLS = ["lg:grid-cols-1", "lg:grid-cols-2", "lg:grid-cols-3"];

/** A titled section of the dialog, the same for every column. */
function Section({
  legend,
  description,
  children,
}: {
  legend: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <FieldSet>
      <FieldLegend>{legend}</FieldLegend>
      <FieldDescription>{description}</FieldDescription>
      {children}
    </FieldSet>
  );
}

interface ConfirmButtonProps {
  icon: LucideIcon;
  label: string;
  title: string;
  description: string;
  onConfirm: () => Promise<void>;
}

/** Footer action that runs behind a confirmation dialog, a failure raising the mutation toast. */
function ConfirmButton({ icon: Icon, label, title, description, onConfirm }: ConfirmButtonProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const action = useMutation({ mutationFn: onConfirm });

  function confirm() {
    setOpen(false);
    action.mutate();
  }

  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <Icon data-icon="inline-start" />
        {label}
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={title}
        description={description}
        confirmLabel={t(($) => $.settings.dialog.clear)}
        onConfirm={confirm}
      />
    </>
  );
}

/**
 * Clear the conversations' temporary files, reporting how many went. They live
 * outside the document tree, so the count is the only evidence the press did
 * anything.
 */
async function clearTmpFiles() {
  const { files_removed } = await clearTmp();
  toast.add({
    type: "success",
    title: i18n.t(($) => $.settings.dialog.clearTmp.done, { count: files_removed }),
  });
}

/** The LLM provider overrides, which the backend defaults fill in when left empty. */
function OverrideFields() {
  const { t } = useTranslation(undefined, DIALOG_T);
  const overrides = useSettingsStore((s) => s.overrides);
  const backendDefaults = useServerSettings();
  const setOverride = useSettingsStore((s) => s.setOverride);
  const hasServerApiKey = backendDefaults?.has_api_key ?? false;

  const fields: {
    key: keyof UserOverrides;
    type?: "password" | "url";
    label: string;
    description: string;
    placeholder: string;
  }[] = [
    {
      key: "model",
      label: t(($) => $.model.label),
      description: t(($) => $.model.description),
      placeholder: backendDefaults?.model || t(($) => $.model.placeholder),
    },
    {
      key: "apiKey",
      type: "password",
      label: t(($) => $.apiKey.label),
      description: hasServerApiKey
        ? t(($) => $.apiKey.serverDescription)
        : t(($) => $.apiKey.description),
      placeholder: hasServerApiKey
        ? t(($) => $.apiKey.serverPlaceholder)
        : t(($) => $.apiKey.placeholder),
    },
    {
      key: "baseUrl",
      type: "url",
      label: t(($) => $.baseUrl.label),
      description: t(($) => $.baseUrl.description),
      placeholder: backendDefaults?.base_url || t(($) => $.baseUrl.placeholder),
    },
    {
      key: "auxModel",
      label: t(($) => $.auxModel.label),
      description: t(($) => $.auxModel.description),
      placeholder: backendDefaults?.aux_model ?? t(($) => $.auxModel.placeholder),
    },
  ];

  return (
    <Section legend={t(($) => $.provider.label)} description={t(($) => $.provider.description)}>
      {fields.map(({ key, type, label, description, placeholder }) => (
        <Field key={key}>
          <FieldLabel htmlFor={`settings-${key}`}>{label}</FieldLabel>
          <Input
            id={`settings-${key}`}
            type={type}
            placeholder={placeholder}
            value={overrides[key]}
            onChange={(e) => setOverride({ [key]: e.target.value })}
          />
          <FieldDescription>{description}</FieldDescription>
        </Field>
      ))}
    </Section>
  );
}

function PersonalityFields() {
  const { t } = useTranslation(undefined, DIALOG_T);
  const { t: tOptions } = useTranslation(undefined, OPTIONS_T);
  const personality = useSettingsStore((s) => s.personality);
  const customSystemMessage = useSettingsStore((s) => s.customSystemMessage);
  const setPersonality = useSettingsStore((s) => s.setPersonality);
  const setCustomSystemMessage = useSettingsStore((s) => s.setCustomSystemMessage);

  const personalities = PersonalitySchema.options.map((value) => ({
    value,
    label: tOptions(($) => $.personality[value]),
  }));

  return (
    <Section legend={t(($) => $.assistant.label)} description={t(($) => $.assistant.description)}>
      <Field>
        <FieldLabel htmlFor="settings-personality">{t(($) => $.personality.label)}</FieldLabel>
        <OptionSelect
          id="settings-personality"
          value={personality}
          options={personalities}
          onChange={setPersonality}
        />
        <FieldDescription>{t(($) => $.personality.description)}</FieldDescription>
      </Field>

      {personality === "custom" && (
        <Field>
          <FieldLabel htmlFor="settings-system-message">
            {t(($) => $.customSystemMessage.label)}
          </FieldLabel>
          <Textarea
            id="settings-system-message"
            placeholder={t(($) => $.customSystemMessage.placeholder)}
            value={customSystemMessage}
            onChange={(e) => setCustomSystemMessage(e.target.value)}
            className="min-h-24"
          />
          <FieldDescription>{t(($) => $.customSystemMessage.description)}</FieldDescription>
        </Field>
      )}
    </Section>
  );
}

/** The built-in tools by group, each with a switch. */
function ToolsFieldSet() {
  const { t } = useTranslation(undefined, DIALOG_T);
  const disabledTools = useSettingsStore((s) => s.toolsSpec.disabledTools);
  const toggleTool = useSettingsStore((s) => s.toggleTool);
  const { data: tools = [] } = useQuery(toolQueries.list());

  if (tools.length === 0) {
    return null;
  }

  const groups = [...Map.groupBy(tools, (tool) => tool.group)];

  return (
    <Section legend={t(($) => $.tools.label)} description={t(($) => $.tools.description)}>
      {groups.map(([group, groupTools]) => (
        <FieldSet key={group} className="gap-3">
          <FieldLegend variant="label" className="capitalize">
            {isCatalogKey(settings.dialog.toolGroups, group)
              ? t(($) => $.toolGroups[group])
              : group}
          </FieldLegend>
          {groupTools.map((tool) => (
            <Field key={tool.name} orientation="horizontal">
              <FieldLabel htmlFor={`tool-${tool.name}`} title={tool.description}>
                {tool.name}
              </FieldLabel>
              <Switch
                id={`tool-${tool.name}`}
                size="sm"
                checked={!disabledTools.includes(tool.name)}
                onCheckedChange={() => toggleTool(tool.name)}
              />
            </Field>
          ))}
        </FieldSet>
      ))}
    </Section>
  );
}

type AuthMode = "none" | "headers" | "oauth2";

interface McpDraft {
  url: string;
  prefix: string;
  auth: AuthMode;
  headers: { key: string; value: string }[];
  oauth2: McpOAuth2Config;
}

const EMPTY_DRAFT: McpDraft = {
  url: "",
  prefix: "",
  auth: "none",
  headers: [],
  oauth2: { clientId: "", clientSecret: "" },
};

/** The form adding an MCP server, with its URL, tool prefix, and authentication. */
function McpServerForm() {
  const { t } = useTranslation(undefined, DIALOG_T);
  const addMcpServer = useSettingsStore((s) => s.addMcpServer);
  const [draft, setDraft] = useState(EMPTY_DRAFT);

  const update = (patch: Partial<McpDraft>) => setDraft((prev) => ({ ...prev, ...patch }));
  const updateHeader = (index: number, patch: Partial<McpDraft["headers"][number]>) =>
    update({ headers: draft.headers.map((h, i) => (i === index ? { ...h, ...patch } : h)) });
  const updateOAuth2 = (patch: Partial<McpOAuth2Config>) =>
    update({ oauth2: { ...draft.oauth2, ...patch } });

  const authModes: { value: AuthMode; label: string }[] = [
    { value: "none", label: t(($) => $.mcp.authNone) },
    { value: "headers", label: t(($) => $.mcp.authHeaders) },
    { value: "oauth2", label: t(($) => $.mcp.authOAuth2) },
  ];

  function add() {
    addMcpServer({
      id: crypto.randomUUID(),
      url: draft.url.trim(),
      toolPrefix: draft.prefix.trim() || undefined,
      headers:
        draft.auth === "headers"
          ? Object.fromEntries(
              draft.headers.filter((h) => h.key.trim()).map((h) => [h.key.trim(), h.value]),
            )
          : {},
      oauth2: draft.auth === "oauth2" ? draft.oauth2 : undefined,
    });
    setDraft(EMPTY_DRAFT);
  }

  return (
    <FieldSet className="gap-3">
      <FieldLegend variant="label">{t(($) => $.mcp.newServer)}</FieldLegend>
      <Field orientation="horizontal">
        <Input
          aria-label={t(($) => $.mcp.url)}
          placeholder="https://mcp-server.example.com/mcp"
          value={draft.url}
          onChange={(e) => update({ url: e.target.value })}
        />
        <Input
          aria-label={t(($) => $.mcp.prefixPlaceholder)}
          placeholder={t(($) => $.mcp.prefixPlaceholder)}
          value={draft.prefix}
          onChange={(e) => update({ prefix: e.target.value })}
          className="w-28"
        />
      </Field>

      <Field orientation="horizontal">
        <FieldLabel htmlFor="mcp-auth">{t(($) => $.mcp.auth)}</FieldLabel>
        <OptionSelect
          id="mcp-auth"
          value={draft.auth}
          options={authModes}
          onChange={(auth) => update({ auth })}
        />
      </Field>

      {draft.auth === "headers" && (
        <div className="flex flex-col gap-2">
          {draft.headers.map((h, i) => (
            <Field key={i} orientation="horizontal">
              <Input
                aria-label={t(($) => $.mcp.headerName)}
                placeholder={t(($) => $.mcp.headerName)}
                value={h.key}
                onChange={(e) => updateHeader(i, { key: e.target.value })}
              />
              <Input
                type="password"
                aria-label={t(($) => $.mcp.headerValue)}
                placeholder={t(($) => $.mcp.headerValue)}
                value={h.value}
                onChange={(e) => updateHeader(i, { value: e.target.value })}
              />
              <TooltipButton
                label={t(($) => $.mcp.removeHeader)}
                size="icon"
                destructive
                onClick={() => update({ headers: draft.headers.filter((_, ii) => ii !== i) })}
              >
                <TrashIcon />
              </TooltipButton>
            </Field>
          ))}
          <Button
            variant="outline"
            size="sm"
            className="self-start"
            onClick={() => update({ headers: [...draft.headers, { key: "", value: "" }] })}
          >
            <PlusIcon data-icon="inline-start" />
            {t(($) => $.mcp.addHeader)}
          </Button>
        </div>
      )}

      {draft.auth === "oauth2" && (
        <div className="flex flex-col gap-2">
          <Input
            aria-label={t(($) => $.mcp.clientId)}
            placeholder={t(($) => $.mcp.clientId)}
            value={draft.oauth2.clientId}
            onChange={(e) => updateOAuth2({ clientId: e.target.value })}
          />
          <Input
            type="password"
            aria-label={t(($) => $.mcp.clientSecret)}
            placeholder={t(($) => $.mcp.clientSecret)}
            value={draft.oauth2.clientSecret}
            onChange={(e) => updateOAuth2({ clientSecret: e.target.value })}
          />
          <Input
            aria-label={t(($) => $.mcp.scopes)}
            placeholder={t(($) => $.mcp.scopes)}
            value={draft.oauth2.scopes ?? ""}
            onChange={(e) => updateOAuth2({ scopes: e.target.value || undefined })}
          />
        </div>
      )}

      <Button
        variant="outline"
        size="sm"
        className="self-start"
        disabled={!draft.url.trim()}
        onClick={add}
      >
        <PlusIcon data-icon="inline-start" />
        {t(($) => $.mcp.addServer)}
      </Button>
    </FieldSet>
  );
}

/** One configured MCP server with its connection test, whose outcome it shows inline. */
function McpServerItem({ server, onRemove }: { server: McpServerEntry; onRemove: () => void }) {
  const { t } = useTranslation(undefined, DIALOG_T);
  const { t: tRoot } = useTranslation();
  const test = useMutation({
    mutationFn: () => testMcpServer(server),
    meta: { inlineError: true },
  });
  const failure = test.error
    ? errorMessage(test.error)
    : test.data?.ok === false
      ? (test.data.error ?? t(($) => $.mcp.connectionFailed))
      : null;

  return (
    <Item variant="outline" size="sm">
      {(Object.keys(server.headers).length > 0 || server.oauth2) && (
        <ItemMedia variant="icon">
          <LockIcon />
        </ItemMedia>
      )}
      <ItemContent className="min-w-0">
        <ItemTitle className="break-all">{server.url}</ItemTitle>
        {server.toolPrefix && (
          <ItemDescription>{t(($) => $.mcp.prefix, { prefix: server.toolPrefix })}</ItemDescription>
        )}
        {failure && <ItemDescription className="text-destructive">{failure}</ItemDescription>}
      </ItemContent>
      <ItemActions>
        {test.isPending && <Spinner aria-label={tRoot(($) => $.common.states.loading)} />}
        {test.data?.ok && (
          <Badge variant="secondary">
            <CheckIcon data-icon="inline-start" />
            {t(($) => $.mcp.toolCount, { count: test.data.tool_count ?? 0 })}
          </Badge>
        )}
        <TooltipButton
          label={t(($) => $.mcp.testConnection)}
          disabled={test.isPending}
          onClick={() => test.mutate()}
        >
          <PlugIcon />
        </TooltipButton>
        <TooltipButton label={t(($) => $.mcp.removeServer)} destructive onClick={onRemove}>
          <TrashIcon />
        </TooltipButton>
      </ItemActions>
    </Item>
  );
}

/** The configured MCP servers, each testable and removable, above the form adding one. */
function McpServersFieldSet() {
  const { t } = useTranslation(undefined, DIALOG_T);
  const servers = useSettingsStore((s) => s.toolsSpec.mcpServers);
  const removeMcpServer = useSettingsStore((s) => s.removeMcpServer);

  return (
    <Section legend={t(($) => $.mcp.label)} description={t(($) => $.mcp.description)}>
      {servers.length > 0 && (
        <ItemGroup>
          {servers.map((server) => (
            // Keyed by identity, so a removal never moves a test result onto its neighbor.
            <McpServerItem
              key={server.id}
              server={server}
              onRemove={() => removeMcpServer(server.id)}
            />
          ))}
        </ItemGroup>
      )}

      <McpServerForm />
    </Section>
  );
}

export function SettingsDialog() {
  const { t } = useTranslation(undefined, DIALOG_T);
  const label = t(($) => $.title);
  const reset = useSettingsStore((s) => s.reset);
  const columns = Number(featureFlags.llmSpec) + 1 + Number(featureFlags.toolsSpec);

  return (
    <Dialog>
      <WithTooltip label={label}>
        <DialogTrigger render={<InputGroupButton size="icon-sm" />}>
          <SettingsIcon />
        </DialogTrigger>
      </WithTooltip>
      <ScrollDialogContent className="sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>{label}</DialogTitle>
          <DialogDescription>{t(($) => $.description)}</DialogDescription>
        </DialogHeader>

        <DialogBody className={cn("grid content-start gap-8", GRID_COLS[columns - 1])}>
          {featureFlags.llmSpec && <OverrideFields />}
          <PersonalityFields />
          {featureFlags.toolsSpec && (
            <FieldGroup>
              <ToolsFieldSet />
              <McpServersFieldSet />
            </FieldGroup>
          )}
        </DialogBody>

        <DialogFooter className="sm:justify-between">
          <div className="flex flex-col gap-2 sm:flex-row">
            <ConfirmButton
              icon={TrashIcon}
              label={t(($) => $.clearMemory.label)}
              title={t(($) => $.clearMemory.title)}
              description={t(($) => $.clearMemory.description)}
              onConfirm={clearMemory}
            />
            <ConfirmButton
              icon={EraserIcon}
              label={t(($) => $.clearTmp.label)}
              title={t(($) => $.clearTmp.title)}
              description={t(($) => $.clearTmp.description)}
              onConfirm={clearTmpFiles}
            />
          </div>
          {featureFlags.llmSpec && (
            <Button variant="outline" onClick={reset}>
              <RotateCcwIcon data-icon="inline-start" />
              {t(($) => $.resetDefaults)}
            </Button>
          )}
        </DialogFooter>
      </ScrollDialogContent>
    </Dialog>
  );
}
