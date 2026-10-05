import { ImagePlus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { usePromptInputAttachments } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { keyPrefix } from "@/i18n";

const T_OPTIONS = keyPrefix(($) => $.chat.composer);

/** Must be rendered inside <PromptInput> to access attachment context. */
export function FileSelectButton() {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const { openFileDialog } = usePromptInputAttachments();

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      title={t(($) => $.attachImageHint)}
      onClick={openFileDialog}
    >
      <ImagePlus className="h-4 w-4" />
      <span className="sr-only">{t(($) => $.attachImage)}</span>
    </Button>
  );
}
