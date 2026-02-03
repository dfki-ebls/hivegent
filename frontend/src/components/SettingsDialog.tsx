import { useState } from 'react';
import { SettingsIcon, PlusIcon, TrashIcon, RotateCcwIcon } from 'lucide-react';
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
import { Textarea } from './ui/textarea';
import { useSettingsStore } from '../stores/settings-store';

export function SettingsDialog() {
  const {
    llm,
    systemPrompt,
    availableModels,
    setLLM,
    setSystemPrompt,
    addModel,
    removeModel,
    reset,
  } = useSettingsStore();

  const [open, setOpen] = useState(false);
  const [newModelName, setNewModelName] = useState('');
  const [newModelValue, setNewModelValue] = useState('');
  const [showAddModel, setShowAddModel] = useState(false);

  const handleAddModel = () => {
    if (newModelName.trim() && newModelValue.trim()) {
      addModel({ name: newModelName.trim(), value: newModelValue.trim() });
      setNewModelName('');
      setNewModelValue('');
      setShowAddModel(false);
    }
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
            Configure your LLM provider settings. These settings are stored
            locally in your browser.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium">Available Models</label>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowAddModel(!showAddModel)}
              >
                <PlusIcon className="h-4 w-4 mr-1" />
                Add Model
              </Button>
            </div>

            {showAddModel && (
              <div className="grid gap-2 p-3 border rounded-md bg-muted/50">
                <Input
                  placeholder="Model name (e.g., My Custom Model)"
                  value={newModelName}
                  onChange={(e) => setNewModelName(e.target.value)}
                />
                <Input
                  placeholder="Model value (e.g., provider/model-name)"
                  value={newModelValue}
                  onChange={(e) => setNewModelValue(e.target.value)}
                />
                <div className="flex gap-2">
                  <Button size="sm" onClick={handleAddModel}>
                    Add
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setShowAddModel(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            )}

            <div className="max-h-32 overflow-y-auto space-y-1">
              {availableModels.map((model) => (
                <div
                  key={model.value}
                  className="flex items-center justify-between py-1 px-2 text-sm rounded hover:bg-muted/50"
                >
                  <div>
                    <span className="font-medium">{model.name}</span>
                    <span className="text-muted-foreground ml-2 text-xs">
                      {model.value}
                    </span>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6"
                    onClick={() => removeModel(model.value)}
                  >
                    <TrashIcon className="h-3 w-3" />
                  </Button>
                </div>
              ))}
            </div>
          </div>

          <div className="grid gap-2">
            <label htmlFor="api-key" className="text-sm font-medium">
              API Key (optional)
            </label>
            <Input
              id="api-key"
              type="password"
              placeholder="Enter your API key (if required)"
              value={llm.apiKey}
              onChange={(e) => setLLM({ apiKey: e.target.value })}
            />
            <p className="text-xs text-muted-foreground">
              Only required for providers that need authentication.
            </p>
          </div>

          <div className="grid gap-2">
            <label htmlFor="base-url" className="text-sm font-medium">
              Base URL
            </label>
            <Input
              id="base-url"
              type="url"
              placeholder="http://localhost:1234/v1"
              value={llm.baseUrl}
              onChange={(e) => setLLM({ baseUrl: e.target.value })}
            />
            <p className="text-xs text-muted-foreground">
              API endpoint for the LLM provider.
            </p>
          </div>

          <div className="grid gap-2">
            <label htmlFor="system-prompt" className="text-sm font-medium">
              System Prompt
            </label>
            <Textarea
              id="system-prompt"
              placeholder="Enter the system prompt..."
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={6}
              className="font-mono text-sm"
            />
            <p className="text-xs text-muted-foreground">
              Instructions that guide the AI's behavior throughout conversations.
            </p>
          </div>
        </div>

        <DialogFooter className="sm:justify-between">
          <Button variant="outline" onClick={reset}>
            <RotateCcwIcon className="h-4 w-4 mr-2" />
            Reset to Defaults
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
