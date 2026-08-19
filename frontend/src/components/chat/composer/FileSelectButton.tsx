import { ImagePlus } from "lucide-react";
import { usePromptInputAttachments } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";

/** Must be rendered inside <PromptInput> to access attachment context. */
export function FileSelectButton() {
  const { openFileDialog } = usePromptInputAttachments();
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      title="Attach an image for the assistant to look at"
      onClick={openFileDialog}
    >
      <ImagePlus className="h-4 w-4" />
      <span className="sr-only">Attach image</span>
    </Button>
  );
}
