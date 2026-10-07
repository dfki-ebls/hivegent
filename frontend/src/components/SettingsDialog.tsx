import {
  CheckIcon,
  EraserIcon,
  LoaderIcon,
  LockIcon,
  PlugIcon,
  PlusIcon,
  RotateCcwIcon,
  SettingsIcon,
  TrashIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { i18n, isCatalogKey, keyPrefix } from "@/i18n";
import { settings } from "@/i18n/locales/en/settings";
import { clearMemory, clearScratch, listTools, type McpTestResult, testMcpServer } from "@/lib/api";
import { featureFlags } from "@/lib/feature-flags";
import {
  type McpOAuth2Config,
  type McpServerEntry,
  type Personality,
  PersonalitySchema,
  type ToolInfo,
} from "@/lib/types";
import { errorMessage } from "@/lib/utils";
import { useSettingsStore } from "@/stores/settings-store";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { FormSection } from "@/components/FormSection";

const DIALOG_T = keyPrefix(($) => $.settings.dialog);
const OPTIONS_T = keyPrefix(($) => $.options);

// --- Footer confirm button ---

interface ConfirmButtonProps {
  icon: React.ReactNode;
  label: string;
  title: string;
  description: string;
  onConfirm: () => Promise<void> | void;
}

/**
 * Footer action that runs behind a confirmation dialog.
 *
 * Awaiting *onConfirm* here is what keeps a failed request from being reported
 * nowhere: every one of these goes through `requestJson`/`requestVoid`, which
 * throw on a failed response, so the toast belongs at the one seam that runs
 * all of them rather than in each action.
 */
function ConfirmButton({ icon, label, title, description, onConfirm }: ConfirmButtonProps) {
  const { t } = useTranslation();
  const run = async () => {
    try {
      await onConfirm();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="outline" size="sm">
          {icon}
          {label}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t(($) => $.common.actions.cancel)}</AlertDialogCancel>
          <AlertDialogAction onClick={() => void run()}>
            {t(($) => $.settings.dialog.clear)}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/**
 * Clear the parked scratch files, reporting what the sweep removed. Scratch is
 * hidden from the document tree, so the count is the only evidence the press
 * did anything.
 */
async function clearScratchFiles() {
  const { files_removed } = await clearScratch();
  toast.success(i18n.t(($) => $.settings.dialog.clearScratch.done, { count: files_removed }));
}

// --- Auth mode types ---

type AuthMode = "none" | "headers" | "oauth2";

// --- Main component ---

export function SettingsDialog() {
  const { t } = useTranslation(undefined, DIALOG_T);
  const { t: tOptions } = useTranslation(undefined, OPTIONS_T);
  const {
    overrides,
    backendDefaults,
    personality,
    customSystemMessage,
    toolsSpec,
    setOverride,
    setPersonality,
    setCustomSystemMessage,
    toggleTool,
    addMcpServer,
    removeMcpServer,
    reset,
  } = useSettingsStore();
  const hasServerApiKey = backendDefaults?.has_api_key ?? false;

  const [open, setOpen] = useState(false);
  const [tools, setTools] = useState<ToolInfo[]>([]);

  // New MCP server form state
  const [newMcpUrl, setNewMcpUrl] = useState("");
  const [newMcpPrefix, setNewMcpPrefix] = useState("");
  const [authMode, setAuthMode] = useState<AuthMode>("none");
  const [headers, setHeaders] = useState<{ key: string; value: string }[]>([]);
  const [oauth2, setOAuth2] = useState<McpOAuth2Config>({
    clientId: "",
    clientSecret: "",
  });

  // Test connection state: index -> result
  const [testResults, setTestResults] = useState<Record<number, McpTestResult | "loading">>({});

  useEffect(() => {
    if (!open || !featureFlags.toolsSpec) return;

    void listTools()
      .then(setTools)
      .catch(() => setTools([]));
  }, [open]);

  const toolsByGroup = tools.reduce<Record<string, ToolInfo[]>>((acc, tool) => {
    (acc[tool.group] ??= []).push(tool);
    return acc;
  }, {});

  const gridColsClass = { 1: "lg:grid-cols-1", 2: "lg:grid-cols-2", 3: "lg:grid-cols-3" }[
    Number(featureFlags.llmSpec) + 1 + Number(featureFlags.toolsSpec)
  ];

  function resetNewMcpForm() {
    setNewMcpUrl("");
    setNewMcpPrefix("");
    setAuthMode("none");
    setHeaders([]);
    setOAuth2({ clientId: "", clientSecret: "" });
  }

  function handleAddMcpServer() {
    const entry: McpServerEntry = {
      url: newMcpUrl.trim(),
      headers: {},
      toolPrefix: newMcpPrefix.trim() || undefined,
    };

    if (authMode === "headers") {
      for (const h of headers) {
        if (h.key.trim()) {
          entry.headers[h.key.trim()] = h.value;
        }
      }
    } else if (authMode === "oauth2") {
      entry.oauth2 = { ...oauth2 };
    }

    addMcpServer(entry);
    resetNewMcpForm();
  }

  function handleTestConnection(index: number) {
    const server = toolsSpec.mcpServers[index];
    setTestResults((prev) => ({ ...prev, [index]: "loading" }));
    void testMcpServer(server).then((result) => {
      setTestResults((prev) => ({ ...prev, [index]: result }));
    });
  }

  /** Whether a server has any auth configured. */
  function hasAuth(server: McpServerEntry): boolean {
    return Object.keys(server.headers).length > 0 || server.oauth2 !== undefined;
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon">
          <SettingsIcon />
          <span className="sr-only">{t(($) => $.title)}</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-5xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t(($) => $.title)}</DialogTitle>
          <DialogDescription>{t(($) => $.description)}</DialogDescription>
        </DialogHeader>

        <div className={`grid ${gridColsClass} gap-6 py-4`}>
          {/* Column 1: model configuration */}
          {featureFlags.llmSpec && (
            <div className="grid gap-4 content-start">
              <FormSection
                label={t(($) => $.model.label)}
                htmlFor="model"
                description={t(($) => $.model.description)}
              >
                <Input
                  id="model"
                  placeholder={backendDefaults?.model || t(($) => $.model.placeholder)}
                  value={overrides.model}
                  onChange={(e) => setOverride({ model: e.target.value })}
                />
              </FormSection>

              <FormSection
                label={t(($) => $.apiKey.label)}
                htmlFor="api-key"
                description={
                  hasServerApiKey
                    ? t(($) => $.apiKey.serverDescription)
                    : t(($) => $.apiKey.description)
                }
              >
                <Input
                  id="api-key"
                  type="password"
                  placeholder={
                    hasServerApiKey
                      ? t(($) => $.apiKey.serverPlaceholder)
                      : t(($) => $.apiKey.placeholder)
                  }
                  value={overrides.apiKey}
                  onChange={(e) => setOverride({ apiKey: e.target.value })}
                />
              </FormSection>

              <FormSection
                label={t(($) => $.baseUrl.label)}
                htmlFor="base-url"
                description={t(($) => $.baseUrl.description)}
              >
                <Input
                  id="base-url"
                  type="url"
                  placeholder={backendDefaults?.base_url || t(($) => $.baseUrl.placeholder)}
                  value={overrides.baseUrl}
                  onChange={(e) => setOverride({ baseUrl: e.target.value })}
                />
              </FormSection>

              <FormSection
                label={t(($) => $.auxModel.label)}
                htmlFor="aux-model"
                description={t(($) => $.auxModel.description)}
              >
                <Input
                  id="aux-model"
                  placeholder={backendDefaults?.aux_model ?? t(($) => $.auxModel.placeholder)}
                  value={overrides.auxModel}
                  onChange={(e) => setOverride({ auxModel: e.target.value })}
                />
              </FormSection>
            </div>
          )}

          {/* Column 2: personality */}
          <div className="grid gap-4 content-start">
            <FormSection
              label={t(($) => $.personality.label)}
              description={t(($) => $.personality.description)}
            >
              <Select value={personality} onValueChange={(v) => setPersonality(v as Personality)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PersonalitySchema.options.map((value) => (
                    <SelectItem key={value} value={value}>
                      {tOptions(($) => $.personality[value])}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormSection>

            {personality === "custom" && (
              <FormSection
                label={t(($) => $.customSystemMessage.label)}
                htmlFor="custom-system-message"
                description={t(($) => $.customSystemMessage.description)}
              >
                <Textarea
                  id="custom-system-message"
                  placeholder={t(($) => $.customSystemMessage.placeholder)}
                  value={customSystemMessage}
                  onChange={(e) => setCustomSystemMessage(e.target.value)}
                  className="min-h-[100px] resize-y"
                />
              </FormSection>
            )}
          </div>

          {/* Column 3: tools and MCP servers */}
          {featureFlags.toolsSpec && (
            <div className="grid gap-4 content-start">
              {tools.length > 0 && (
                <FormSection
                  label={t(($) => $.tools.label)}
                  description={t(($) => $.tools.description)}
                >
                  <div className="grid gap-3">
                    {Object.entries(toolsByGroup).map(([group, groupTools]) => (
                      <div key={group}>
                        <p className="text-xs font-medium text-muted-foreground capitalize mb-1.5">
                          {isCatalogKey(settings.dialog.toolGroups, group)
                            ? t(($) => $.toolGroups[group])
                            : group}
                        </p>
                        <div className="grid gap-1.5">
                          {groupTools.map((tool) => (
                            <div
                              key={tool.name}
                              className="flex items-center justify-between gap-2"
                            >
                              <Label
                                htmlFor={`tool-${tool.name}`}
                                className="text-xs font-normal cursor-pointer"
                                title={tool.description}
                              >
                                {tool.name}
                              </Label>
                              <Switch
                                id={`tool-${tool.name}`}
                                checked={!toolsSpec.disabledTools.includes(tool.name)}
                                onCheckedChange={() => toggleTool(tool.name)}
                              />
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </FormSection>
              )}

              <FormSection label={t(($) => $.mcp.label)} description={t(($) => $.mcp.description)}>
                <div className="grid gap-2">
                  {/* Existing servers */}
                  {toolsSpec.mcpServers.map((server, index) => {
                    const result = testResults[index];
                    return (
                      <div
                        key={index}
                        className="flex items-center gap-2 rounded-md border px-3 py-2 text-xs"
                      >
                        {hasAuth(server) && (
                          <LockIcon className="size-3 text-muted-foreground shrink-0" />
                        )}
                        <span className="truncate flex-1" title={server.url}>
                          {server.url}
                        </span>
                        {server.toolPrefix && (
                          <span className="text-muted-foreground shrink-0">
                            {t(($) => $.mcp.prefix, { prefix: server.toolPrefix })}
                          </span>
                        )}
                        {/* Test result indicator */}
                        {result === "loading" && (
                          <LoaderIcon className="size-3 animate-spin text-muted-foreground shrink-0" />
                        )}
                        {result !== undefined && result !== "loading" && result.ok && (
                          <span className="flex items-center gap-0.5 text-green-600 shrink-0">
                            <CheckIcon className="size-3" />
                            {result.tool_count}
                          </span>
                        )}
                        {result !== undefined && result !== "loading" && !result.ok && (
                          <span
                            className="flex items-center gap-0.5 text-red-600 shrink-0"
                            title={result.error ?? t(($) => $.mcp.connectionFailed)}
                          >
                            <XIcon className="size-3" />
                          </span>
                        )}
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          title={t(($) => $.mcp.testConnection)}
                          aria-label={t(($) => $.mcp.testConnection)}
                          disabled={result === "loading"}
                          onClick={() => handleTestConnection(index)}
                        >
                          <PlugIcon />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          title={t(($) => $.mcp.removeServer)}
                          aria-label={t(($) => $.mcp.removeServer)}
                          onClick={() => removeMcpServer(index)}
                        >
                          <TrashIcon />
                        </Button>
                      </div>
                    );
                  })}

                  {/* Add new server form */}
                  <div className="grid gap-2 rounded-md border p-3">
                    <div className="flex items-end gap-2">
                      <div className="grid gap-1 flex-1">
                        <Input
                          placeholder="https://mcp-server.example.com/mcp"
                          value={newMcpUrl}
                          onChange={(e) => setNewMcpUrl(e.target.value)}
                          className="text-xs"
                        />
                      </div>
                      <div className="grid gap-1 w-28">
                        <Input
                          placeholder={t(($) => $.mcp.prefixPlaceholder)}
                          value={newMcpPrefix}
                          onChange={(e) => setNewMcpPrefix(e.target.value)}
                          className="text-xs"
                        />
                      </div>
                    </div>

                    {/* Auth mode selector */}
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">{t(($) => $.mcp.auth)}</span>
                      <Select value={authMode} onValueChange={(v) => setAuthMode(v as AuthMode)}>
                        <SelectTrigger className="h-7 text-xs w-auto">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="none">{t(($) => $.mcp.authNone)}</SelectItem>
                          <SelectItem value="headers">{t(($) => $.mcp.authHeaders)}</SelectItem>
                          <SelectItem value="oauth2">{t(($) => $.mcp.authOAuth2)}</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>

                    {/* Headers editor */}
                    {authMode === "headers" && (
                      <div className="grid gap-1.5">
                        {headers.map((h, i) => (
                          <div key={i} className="flex items-center gap-1.5">
                            <Input
                              placeholder={t(($) => $.mcp.headerName)}
                              value={h.key}
                              onChange={(e) =>
                                setHeaders((prev) =>
                                  prev.map((hh, ii) =>
                                    ii === i ? { ...hh, key: e.target.value } : hh,
                                  ),
                                )
                              }
                              className="text-xs flex-1"
                            />
                            <Input
                              type="password"
                              placeholder={t(($) => $.mcp.headerValue)}
                              value={h.value}
                              onChange={(e) =>
                                setHeaders((prev) =>
                                  prev.map((hh, ii) =>
                                    ii === i ? { ...hh, value: e.target.value } : hh,
                                  ),
                                )
                              }
                              className="text-xs flex-1"
                            />
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              title={t(($) => $.mcp.removeHeader)}
                              aria-label={t(($) => $.mcp.removeHeader)}
                              onClick={() => setHeaders((prev) => prev.filter((_, ii) => ii !== i))}
                            >
                              <TrashIcon />
                            </Button>
                          </div>
                        ))}
                        <Button
                          variant="outline"
                          size="xs"
                          className="w-fit"
                          onClick={() => setHeaders((prev) => [...prev, { key: "", value: "" }])}
                        >
                          <PlusIcon />
                          {t(($) => $.mcp.addHeader)}
                        </Button>
                      </div>
                    )}

                    {/* OAuth2 Client Credentials form */}
                    {authMode === "oauth2" && (
                      <div className="grid gap-1.5">
                        <Input
                          placeholder={t(($) => $.mcp.clientId)}
                          value={oauth2.clientId}
                          onChange={(e) =>
                            setOAuth2((prev) => ({ ...prev, clientId: e.target.value }))
                          }
                          className="text-xs"
                        />
                        <Input
                          type="password"
                          placeholder={t(($) => $.mcp.clientSecret)}
                          value={oauth2.clientSecret}
                          onChange={(e) =>
                            setOAuth2((prev) => ({ ...prev, clientSecret: e.target.value }))
                          }
                          className="text-xs"
                        />
                        <Input
                          placeholder={t(($) => $.mcp.scopes)}
                          value={oauth2.scopes ?? ""}
                          onChange={(e) =>
                            setOAuth2((prev) => ({
                              ...prev,
                              scopes: e.target.value || undefined,
                            }))
                          }
                          className="text-xs"
                        />
                      </div>
                    )}

                    <Button
                      variant="outline"
                      size="xs"
                      className="w-fit"
                      disabled={!newMcpUrl.trim()}
                      onClick={handleAddMcpServer}
                    >
                      <PlusIcon />
                      {t(($) => $.mcp.addServer)}
                    </Button>
                  </div>
                </div>
              </FormSection>
            </div>
          )}
        </div>

        <DialogFooter className="flex-row justify-between sm:justify-between">
          <div className="flex gap-2">
            <ConfirmButton
              icon={<TrashIcon />}
              label={t(($) => $.clearMemory.label)}
              title={t(($) => $.clearMemory.title)}
              description={t(($) => $.clearMemory.description)}
              onConfirm={clearMemory}
            />
            <ConfirmButton
              icon={<EraserIcon />}
              label={t(($) => $.clearScratch.label)}
              title={t(($) => $.clearScratch.title)}
              description={t(($) => $.clearScratch.description)}
              onConfirm={clearScratchFiles}
            />
          </div>
          {featureFlags.llmSpec && (
            <Button variant="outline" size="sm" onClick={reset}>
              <RotateCcwIcon />
              {t(($) => $.resetDefaults)}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
