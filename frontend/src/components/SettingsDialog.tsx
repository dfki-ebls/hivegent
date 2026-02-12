import { useState } from 'react';
import { SettingsIcon, Trash2Icon, RotateCcwIcon } from 'lucide-react';

import { clearAllStorage } from '../stores/storage';
import { useSettingsStore } from '../stores/settings-store';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from './ui/dialog';
import { Input } from './ui/input';

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
    setLLM,
    setSmallModel,
    setVisionModel,
    reset,
  } = useSettingsStore();

  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon">
          <SettingsIcon className="h-4 w-4" />
          <span className="sr-only">Settings</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>LLM Settings</DialogTitle>
          <DialogDescription>
            Configure your LLM provider settings. These settings are stored locally in your browser.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-4">
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
            description={hasServerApiKey
              ? 'Server API key configured. Override it here or leave empty to use the server key.'
              : 'Only required for providers that need authentication.'}
          >
            <Input
              id="api-key"
              type="password"
              placeholder={hasServerApiKey ? 'Using server API key' : 'Enter your API key (if required)'}
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

          <div className="pt-2 border-t grid gap-4">
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
        </div>

        <DialogFooter className="sm:justify-between">
          <div className="flex gap-2">
            <Button variant="outline" onClick={reset}>
              <RotateCcwIcon className="h-4 w-4 mr-2" />
              Reset to Server Defaults
            </Button>
            <Button variant="destructive" onClick={clearAllStorage}>
              <Trash2Icon className="h-4 w-4 mr-2" />
              Clear All Local Data
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
