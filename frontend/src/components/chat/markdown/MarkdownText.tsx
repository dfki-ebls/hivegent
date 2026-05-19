import { MessageResponse } from "@/components/ai-elements/message";
import {
  CITATION_ALLOWED_TAGS,
  CITATION_COMPONENTS,
  streamdownPlugins,
} from "@/components/chat/markdown/plugins";
import { normalizeMathDelimiters } from "@/lib/normalize-math";

interface MarkdownTextProps {
  children: string;
}

export function MarkdownText({ children }: MarkdownTextProps) {
  return (
    <MessageResponse
      allowedTags={CITATION_ALLOWED_TAGS}
      components={CITATION_COMPONENTS}
      plugins={streamdownPlugins}
    >
      {normalizeMathDelimiters(children)}
    </MessageResponse>
  );
}
