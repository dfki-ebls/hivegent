import { Paperclip } from "lucide-react";
import { usePromptInputAttachments } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";

/** Must be rendered inside <PromptInput> to access attachment context. */
export function FileSelectButton() {
  const { openFileDialog } = usePromptInputAttachments();
  return (
    <Button variant="ghost" size="icon" onClick={openFileDialog}>
      <Paperclip className="h-4 w-4" />
      <span className="sr-only">Attach file</span>
    </Button>
  );
}
