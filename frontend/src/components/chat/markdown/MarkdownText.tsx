import { useMemo } from "react";
import { MessageResponse } from "@/components/ai-elements/message";
import {
  CITATION_ALLOWED_TAGS,
  CITATION_COMPONENTS,
  streamdownPlugins,
} from "@/components/chat/markdown/plugins";
import { normalizeMath } from "@/lib/normalize-math";
import { normalizeVoidTags } from "@/lib/normalize-void-tags";

interface MarkdownTextProps {
  children: string;
}

export function MarkdownText({ children }: MarkdownTextProps) {
  const normalized = useMemo(
    () => normalizeVoidTags(normalizeMath(children)),
    [children],
  );

  return (
    <MessageResponse
      allowedTags={CITATION_ALLOWED_TAGS}
      components={CITATION_COMPONENTS}
      plugins={streamdownPlugins}
    >
      {normalized}
    </MessageResponse>
  );
}
