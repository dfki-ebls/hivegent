import { X } from "lucide-react";
import {
  PromptInputHeader,
  usePromptInputAttachments,
} from "@/components/ai-elements/prompt-input";

/** Renders thumbnails for attached images. Must be inside <PromptInput>. */
export function AttachedFiles() {
  const { files, remove } = usePromptInputAttachments();
  if (files.length === 0) return null;
  return (
    <PromptInputHeader>
      {files.map((file) => (
        <div key={file.id} className="group relative">
          <img
            src={file.url}
            alt={file.filename ?? "Attached image"}
            title={file.filename}
            className="h-14 w-14 rounded-md border object-cover"
          />
          <button
            type="button"
            aria-label={`Remove ${file.filename ?? "image"}`}
            className="absolute -right-1.5 -top-1.5 rounded-full border bg-background p-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
            onClick={() => remove(file.id)}
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      ))}
    </PromptInputHeader>
  );
}
