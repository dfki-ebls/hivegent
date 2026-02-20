import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Streamdown } from "streamdown";

import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { Input } from "./ui/input";
import { ScrollArea } from "./ui/scroll-area";
import { Textarea } from "./ui/textarea";

interface DocumentPreviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filename: string;
  content: string | null;
  isLoading?: boolean;
  editable?: boolean;
  onSave?: (filename: string, content: string) => Promise<void>;
}

export function DocumentPreviewDialog({
  open,
  onOpenChange,
  filename: initialFilename,
  content: initialContent,
  isLoading = false,
  editable = false,
  onSave,
}: DocumentPreviewDialogProps) {
  const [filename, setFilename] = useState(initialFilename);
  const [content, setContent] = useState(initialContent ?? "");
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    setFilename(initialFilename);
    setContent(initialContent ?? "");
  }, [initialFilename, initialContent]);

  const handleSave = async () => {
    if (!onSave || !filename.trim()) return;
    setIsSaving(true);
    try {
      await onSave(filename.trim(), content);
      onOpenChange(false);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="h-[90vh] w-[90vw] max-w-4xl! flex flex-col">
        <DialogHeader>
          {editable ? (
            <>
              <DialogTitle className="sr-only">Edit document</DialogTitle>
              <Input
                value={filename}
                onChange={(e) => setFilename(e.target.value)}
                placeholder="filename.md"
                className="text-lg font-semibold"
              />
            </>
          ) : (
            <DialogTitle>{filename}</DialogTitle>
          )}
          <DialogDescription className="sr-only">
            {editable ? "Edit document content" : `Preview of ${initialFilename}`}
          </DialogDescription>
        </DialogHeader>
        {isLoading ? (
          <div className="flex flex-1 items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : editable ? (
          <Textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="Write your markdown content here..."
            className="flex-1 min-h-0 resize-none font-mono text-sm"
          />
        ) : (
          <ScrollArea className="flex-1 min-h-0 w-full">
            <div className="prose prose-sm dark:prose-invert max-w-none p-4">
              <Streamdown>{content}</Streamdown>
            </div>
          </ScrollArea>
        )}
        {editable && (
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleSave}
              disabled={isSaving || !filename.trim()}
            >
              {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
