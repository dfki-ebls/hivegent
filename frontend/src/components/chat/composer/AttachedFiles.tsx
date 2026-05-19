import { Paperclip, X } from "lucide-react";
import {
  PromptInputHeader,
  usePromptInputAttachments,
} from "@/components/ai-elements/prompt-input";
import { Badge } from "@/components/ui/badge";

/** Renders badges for attached files. Must be inside <PromptInput>. */
export function AttachedFiles() {
  const { files, remove } = usePromptInputAttachments();
  if (files.length === 0) return null;
  return (
    <PromptInputHeader>
      {files.map((file) => (
        <Badge key={file.id} variant="outline" className="gap-1 text-xs">
          <Paperclip className="h-3 w-3" />
          {file.filename}
          <button
            type="button"
            className="ml-0.5 rounded-full hover:bg-muted"
            onClick={() => remove(file.id)}
          >
            <X className="h-3 w-3" />
          </button>
        </Badge>
      ))}
    </PromptInputHeader>
  );
}
