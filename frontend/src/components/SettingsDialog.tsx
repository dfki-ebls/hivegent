import { PlusIcon, RotateCcwIcon, SettingsIcon, TrashIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchTools } from "../lib/api";
import { PERSONALITY_OPTIONS, type Personality, type ToolInfo } from "../lib/types";
import { useSettingsStore } from "../stores/settings-store";
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

// --- Main component ---

export function SettingsDialog() {
  const {
    llm,
    smallModel,
    visionModel,
    hasServerApiKey,
    personality,
    customSystemMessage,
    toolsSpec,
    setLLM,
    setSmallModel,
    setVisionModel,
    setPersonality,
    setCustomSystemMessage,
    toggleTool,
    addMcpServer,
    removeMcpServer,
    reset,
  } = useSettingsStore();

  const [open, setOpen] = useState(false);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [newMcpUrl, setNewMcpUrl] = useState("");
  const [newMcpPrefix, setNewMcpPrefix] = useState("");

  useEffect(() => {
    if (!open) return;
    void fetchTools().then(setTools).catch(() => setTools([]));
  }, [open]);

  const toolsByGroup = tools.reduce<Record<string, ToolInfo[]>>((acc, tool) => {
    (acc[tool.group] ??= []).push(tool);
    return acc;
  }, {});

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon">
          <SettingsIcon className="h-4 w-4" />
          <span className="sr-only">Settings</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>
            Configure your LLM provider and assistant settings. These settings are stored locally in
            your browser.
          </DialogDescription>
        </DialogHeader>

        <div className="grid md:grid-cols-2 gap-6 py-4">
          {/* Left column — Model Configuration */}
          <div className="grid gap-4">
            <SettingsSection
              label="Model"
              htmlFor="model"
              description="The main model to use for chat. Leave empty to use the server default."
            >
              <Input
                id="model"
                placeholder="e.g., openai/gpt-4o"
                value={llm.model}
                onChange={(e) => setLLM({ model: e.target.value })}
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
                value={llm.apiKey}
                onChange={(e) => setLLM({ apiKey: e.target.value })}
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
                placeholder="e.g., http://localhost:11434/v1"
                value={llm.baseUrl}
                onChange={(e) => setLLM({ baseUrl: e.target.value })}
              />
            </SettingsSection>

            <SettingsSection
              label="Small Model (optional)"
              htmlFor="small-model"
              description="A smaller model for lightweight tasks like title generation. Uses the same provider settings as the main model."
            >
              <Input
                id="small-model"
                placeholder="e.g., qwen/qwen3-8b"
                value={smallModel}
                onChange={(e) => setSmallModel(e.target.value)}
              />
            </SettingsSection>

            <SettingsSection
              label="Vision Model (optional)"
              htmlFor="vision-model"
              description="A vision-capable model for document conversion (PDF, images). Uses the same provider settings as the main model."
            >
              <Input
                id="vision-model"
                placeholder="e.g., openai/gpt-4o"
                value={visionModel}
                onChange={(e) => setVisionModel(e.target.value)}
              />
            </SettingsSection>
          </div>

          {/* Right column — Customization */}
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
              </SettingsSection>
            )}

            <SettingsSection
              label="MCP Servers"
              description="Connect external tool servers via the Model Context Protocol (HTTP transport)."
            >
              <div className="grid gap-2">
                {toolsSpec.mcpServers.map((server, index) => (
                  <div
                    key={index}
                    className="flex items-center gap-2 rounded-md border px-3 py-2 text-xs"
                  >
                    <span className="truncate flex-1" title={server.url}>
                      {server.url}
                    </span>
                    {server.toolPrefix && (
                      <span className="text-muted-foreground shrink-0">
                        prefix: {server.toolPrefix}
                      </span>
                    )}
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 shrink-0"
                      onClick={() => removeMcpServer(index)}
                    >
                      <TrashIcon className="h-3 w-3" />
                    </Button>
                  </div>
                ))}
                <div className="flex items-end gap-2">
                  <div className="grid gap-1 flex-1">
                    <Input
                      placeholder="https://mcp-server.example.com/sse"
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
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-9 w-9 shrink-0"
                    disabled={!newMcpUrl.trim()}
                    onClick={() => {
                      addMcpServer({
                        url: newMcpUrl.trim(),
                        headers: {},
                        toolPrefix: newMcpPrefix.trim() || undefined,
                      });
                      setNewMcpUrl("");
                      setNewMcpPrefix("");
                    }}
                  >
                    <PlusIcon className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </SettingsSection>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={reset}>
            <RotateCcwIcon className="h-4 w-4 mr-2" />
            Reset to Server Defaults
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
