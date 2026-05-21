import { CopyIcon, RefreshCcwIcon } from "lucide-react";
import { MessageAction, MessageActions } from "@/components/ai-elements/message";
import { MarkdownText } from "@/components/chat/markdown/MarkdownText";

interface TextPartProps {
  text: string;
  showActions: boolean;
  onRegenerate: () => void;
}

export function TextPart({ text, showActions, onRegenerate }: TextPartProps) {
  return (
    <>
      <MarkdownText>{text}</MarkdownText>
      {showActions && (
        <MessageActions>
          <MessageAction onClick={onRegenerate} label="Retry">
            <RefreshCcwIcon className="size-3" />
          </MessageAction>
          <MessageAction onClick={() => void navigator.clipboard.writeText(text)} label="Copy">
            <CopyIcon className="size-3" />
          </MessageAction>
        </MessageActions>
      )}
    </>
  );
}
