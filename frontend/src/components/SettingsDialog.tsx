import { useState } from 'react';
import { SettingsIcon, PlusIcon, TrashIcon, Trash2Icon, RotateCcwIcon } from 'lucide-react';

import { clearAllStorage } from '../stores/storage';
import { useSettingsStore, type ModelConfig } from '../stores/settings-store';
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

// --- Add model form ---

interface AddModelFormProps {
  onAdd: (model: ModelConfig) => void;
  onCancel: () => void;
}

function AddModelForm({ onAdd, onCancel }: AddModelFormProps) {
  const [name, setName] = useState('');
  const [value, setValue] = useState('');

  const handleSubmit = () => {
    if (name.trim() && value.trim()) {
      onAdd({ name: name.trim(), value: value.trim() });
    }
  };

  return (
    <div className="grid gap-2 p-3 border rounded-md bg-muted/50">
      <Input
        placeholder="Model name (e.g., My Custom Model)"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <Input
        placeholder="Model value (e.g., provider/model-name)"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <div className="flex gap-2">
        <Button size="sm" onClick={handleSubmit}>
          Add
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

// --- Model list item ---

interface ModelListItemProps {
  model: ModelConfig;
  onRemove: () => void;
}

function ModelListItem({ model, onRemove }: ModelListItemProps) {
  return (
    <div className="flex items-center justify-between py-1 px-2 text-sm rounded hover:bg-muted/50">
      <div>
        <span className="font-medium">{model.name}</span>
        <span className="text-muted-foreground ml-2 text-xs">{model.value}</span>
      </div>
      <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onRemove}>
        <TrashIcon className="h-3 w-3" />
      </Button>
    </div>
  );
}

// --- Main component ---

export function SettingsDialog() {
  const {
    llm,
    smallModel,
    visionModel,
    availableModels,
    hasServerApiKey,
    setLLM,
    setSmallModel,
    setVisionModel,
    addModel,
    removeModel,
    reset,
  } = useSettingsStore();

  const [open, setOpen] = useState(false);
  const [showAddModel, setShowAddModel] = useState(false);

  const handleAddModel = (model: ModelConfig) => {
    addModel(model);
    setShowAddModel(false);
  };

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
          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium">Available Models</label>
              <Button variant="ghost" size="sm" onClick={() => setShowAddModel(!showAddModel)}>
                <PlusIcon className="h-4 w-4 mr-1" />
                Add Model
              </Button>
            </div>

            {showAddModel && (
              <AddModelForm onAdd={handleAddModel} onCancel={() => setShowAddModel(false)} />
            )}

            <div className="max-h-32 overflow-y-auto space-y-1">
              {availableModels.map((model) => (
                <ModelListItem
                  key={model.value}
                  model={model}
                  onRemove={() => removeModel(model.value)}
                />
              ))}
            </div>
          </div>

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
              placeholder="http://localhost:1234/v1"
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
