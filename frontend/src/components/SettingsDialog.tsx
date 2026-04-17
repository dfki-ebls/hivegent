import {
  CheckIcon,
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
import { clearMemory, listTools, type McpTestResult, testMcpServer } from "../lib/api";
import {
  PERSONALITY_OPTIONS,
  type McpOAuth2Config,
  type McpServerEntry,
  type Personality,
  type ToolInfo,
} from "../lib/types";
import { useSettingsStore } from "../stores/settings-store";
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
} from "./ui/alert-dialog";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "./ui/dialog";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Switch } from "./ui/switch";
import { Textarea } from "./ui/textarea";

// --- Section components ---

interface SettingsSectionProps {
  label: string;
  htmlFor?: string;
  description?: string;
  children: React.ReactNode;
}

function SettingsSection({ label, htmlFor, description, children }: SettingsSectionProps) {
  return (
    <div className="grid gap-2">
      <label htmlFor={htmlFor} className="text-sm font-medium">
        {label}
      </label>
      {children}
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
    </div>
  );
}

// --- Auth mode types ---

type AuthMode = "none" | "headers" | "oauth2";

// --- Main component ---

export function SettingsDialog() {
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
    if (!open) return;
    void listTools()
      .then(setTools)
      .catch(() => setTools([]));
  }, [open]);

  const toolsByGroup = tools.reduce<Record<string, ToolInfo[]>>((acc, tool) => {
    (acc[tool.group] ??= []).push(tool);
    return acc;
  }, {});

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
          <SettingsIcon className="h-4 w-4" />
          <span className="sr-only">Settings</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-5xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>
            Configure your LLM provider and assistant settings. These settings are stored locally in
            your browser.
          </DialogDescription>
        </DialogHeader>

        <div className="grid lg:grid-cols-3 gap-6 py-4">
          {/* Column 1 — Model Configuration */}
          <div className="grid gap-4 content-start">
            <SettingsSection
              label="Model"
              htmlFor="model"
              description="The main model to use for chat. Leave empty to use the server default."
            >
              <Input
                id="model"
                placeholder={backendDefaults?.model || "e.g., openai/gpt-4o"}
                value={overrides.model}
                onChange={(e) => setOverride({ model: e.target.value })}
              />
            </SettingsSection>

            <SettingsSection
              label="API Key (optional)"
              htmlFor="api-key"
              description={
                hasServerApiKey
                  ? "Server API key configured. Override it here or leave empty to use the server key."
                  : "Only required for providers that need authentication."
              }
            >
              <Input
                id="api-key"
                type="password"
                placeholder={
                  hasServerApiKey ? "Using server API key" : "Enter your API key (if required)"
                }
                value={overrides.apiKey}
                onChange={(e) => setOverride({ apiKey: e.target.value })}
              />
            </SettingsSection>

            <SettingsSection
              label="Base URL"
              htmlFor="base-url"
              description="API endpoint for the LLM provider."
            >
              <Input
                id="base-url"
                type="url"
                placeholder={backendDefaults?.base_url || "e.g., http://localhost:11434/v1"}
                value={overrides.baseUrl}
                onChange={(e) => setOverride({ baseUrl: e.target.value })}
              />
            </SettingsSection>

            <SettingsSection
              label="Auxiliary Model (optional)"
              htmlFor="aux-model"
              description="Must be small, fast, and vision-capable. Drives document conversion, alt-text generation, title generation, compaction, subagent exploration, and LLM-guided chunking — all high-volume workloads where cost and latency matter more than reasoning depth. Uses the same provider settings as the main model."
            >
              <Input
                id="aux-model"
                placeholder={backendDefaults?.aux_model ?? "e.g., openai/gpt-4o-mini"}
                value={overrides.auxModel}
                onChange={(e) => setOverride({ auxModel: e.target.value })}
              />
            </SettingsSection>
          </div>

          {/* Column 2 — Personality */}
          <div className="grid gap-4 content-start">
            <SettingsSection label="Personality" description="Choose how the assistant responds.">
              <Select value={personality} onValueChange={(v) => setPersonality(v as Personality)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PERSONALITY_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </SettingsSection>

            {personality === "custom" && (
              <SettingsSection
                label="Custom System Message"
                htmlFor="custom-system-message"
                description="Provide your own system instructions for the assistant."
              >
                <Textarea
                  id="custom-system-message"
                  placeholder="You are a helpful assistant that..."
                  value={customSystemMessage}
                  onChange={(e) => setCustomSystemMessage(e.target.value)}
                  className="min-h-[100px] resize-y"
                />
              </SettingsSection>
            )}
          </div>

          {/* Column 3 — Tools + MCP Servers */}
          <div className="grid gap-4 content-start">
            {tools.length > 0 && (
              <SettingsSection
                label="Tools"
                description="Toggle which tools the assistant can use."
              >
                <div className="grid gap-3">
                  {Object.entries(toolsByGroup).map(([group, groupTools]) => (
                    <div key={group}>
                      <p className="text-xs font-medium text-muted-foreground capitalize mb-1.5">
                        {group}
                      </p>
                      <div className="grid gap-1.5">
                        {groupTools.map((tool) => (
                          <div key={tool.name} className="flex items-center justify-between gap-2">
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
              </SettingsSection>
            )}

            <SettingsSection
              label="MCP Servers"
              description='Connect external tool servers via the Model Context Protocol (Streamable HTTP transport). A prefix namespaces all tools from a server (e.g., prefix "jira" turns "search" into "jira_search"), preventing name collisions when multiple servers provide similarly-named tools.'
            >
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
                        <LockIcon className="h-3 w-3 text-muted-foreground shrink-0" />
                      )}
                      <span className="truncate flex-1" title={server.url}>
                        {server.url}
                      </span>
                      {server.toolPrefix && (
                        <span className="text-muted-foreground shrink-0">
                          prefix: {server.toolPrefix}
                        </span>
                      )}
                      {/* Test result indicator */}
                      {result === "loading" && (
                        <LoaderIcon className="h-3 w-3 animate-spin text-muted-foreground shrink-0" />
                      )}
                      {result !== undefined && result !== "loading" && result.ok && (
                        <span className="flex items-center gap-0.5 text-green-600 shrink-0">
                          <CheckIcon className="h-3 w-3" />
                          {result.tool_count}
                        </span>
                      )}
                      {result !== undefined && result !== "loading" && !result.ok && (
                        <span
                          className="flex items-center gap-0.5 text-red-600 shrink-0"
                          title={result.error ?? "Connection failed"}
                        >
                          <XIcon className="h-3 w-3" />
                        </span>
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 shrink-0"
                        title="Test connection"
                        disabled={result === "loading"}
                        onClick={() => handleTestConnection(index)}
                      >
                        <PlugIcon className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 shrink-0"
                        onClick={() => removeMcpServer(index)}
                      >
                        <TrashIcon className="h-3 w-3" />
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
                        placeholder="Prefix"
                        value={newMcpPrefix}
                        onChange={(e) => setNewMcpPrefix(e.target.value)}
                        className="text-xs"
                      />
                    </div>
                  </div>

                  {/* Auth mode selector */}
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">Auth:</span>
                    <Select value={authMode} onValueChange={(v) => setAuthMode(v as AuthMode)}>
                      <SelectTrigger className="h-7 text-xs w-auto">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">None</SelectItem>
                        <SelectItem value="headers">Headers</SelectItem>
                        <SelectItem value="oauth2">OAuth2 Client Credentials</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Headers editor */}
                  {authMode === "headers" && (
                    <div className="grid gap-1.5">
                      {headers.map((h, i) => (
                        <div key={i} className="flex items-center gap-1.5">
                          <Input
                            placeholder="Header name"
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
                            placeholder="Value"
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
                            size="icon"
                            className="h-7 w-7 shrink-0"
                            onClick={() => setHeaders((prev) => prev.filter((_, ii) => ii !== i))}
                          >
                            <TrashIcon className="h-3 w-3" />
                          </Button>
                        </div>
                      ))}
                      <Button
                        variant="outline"
                        size="sm"
                        className="text-xs w-fit"
                        onClick={() => setHeaders((prev) => [...prev, { key: "", value: "" }])}
                      >
                        <PlusIcon className="h-3 w-3 mr-1" />
                        Add Header
                      </Button>
                    </div>
                  )}

                  {/* OAuth2 Client Credentials form */}
                  {authMode === "oauth2" && (
                    <div className="grid gap-1.5">
                      <Input
                        placeholder="Client ID"
                        value={oauth2.clientId}
                        onChange={(e) =>
                          setOAuth2((prev) => ({ ...prev, clientId: e.target.value }))
                        }
                        className="text-xs"
                      />
                      <Input
                        type="password"
                        placeholder="Client Secret"
                        value={oauth2.clientSecret}
                        onChange={(e) =>
                          setOAuth2((prev) => ({ ...prev, clientSecret: e.target.value }))
                        }
                        className="text-xs"
                      />
                      <Input
                        placeholder="Scopes (optional, space-separated)"
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
                    size="sm"
                    className="text-xs w-fit"
                    disabled={!newMcpUrl.trim()}
                    onClick={handleAddMcpServer}
                  >
                    <PlusIcon className="h-3 w-3 mr-1" />
                    Add Server
                  </Button>
                </div>
              </div>
            </SettingsSection>
          </div>
        </div>

        <DialogFooter className="flex-row justify-between sm:justify-between">
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="outline" size="sm">
                <TrashIcon className="h-4 w-4 mr-2" />
                Clear Memory
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Clear memory?</AlertDialogTitle>
                <AlertDialogDescription>
                  This will permanently delete all saved memory. The assistant will no longer
                  remember information from previous conversations.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={() => void clearMemory()}>Clear</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <Button variant="outline" size="sm" onClick={reset}>
            <RotateCcwIcon className="h-4 w-4 mr-2" />
            Reset to Server Defaults
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
