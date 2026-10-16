import { type ComponentProps, useMemo } from "react";
import {
  type Components,
  defaultComponents,
  type ExtraProps,
  Streamdown,
  useIsCodeFenceIncomplete,
} from "streamdown";
import { Citation } from "@/components/Citation";
import { HighlightedCode } from "@/components/HighlightedCode";
import { ImageRef } from "@/components/ImageRef";
import { STREAMDOWN_PLUGINS } from "@/components/chat/markdown/config";
import { useMessageStreaming } from "@/hooks/chat/use-message-streaming";
import { normalizeDisplayMath, normalizeMath } from "@/lib/normalize-math";
import { normalizeVoidTags } from "@/lib/normalize-void-tags";

const CLASS_NAME = "size-full [&>*:first-child]:mt-0 [&>*:last-child]:mb-0";

function FencedCode({ code, language }: { code: string; language: string }) {
  // The copy button waits for the closing fence, so it never copies half the code.
  const incomplete = useIsCodeFenceIncomplete();

  return (
    <HighlightedCode
      code={code}
      language={language}
      title={language || "text"}
      copyable={!incomplete}
      size="default"
      className="my-4"
    />
  );
}

/**
 * Fenced code as a `CodeBlock`, highlighted by the shared Shiki core.
 * Streamdown marks fenced code with `data-block`, so inline code and Mermaid
 * diagrams keep its own rendering.
 */
function MarkdownCode(props: ComponentProps<"code"> & ExtraProps) {
  const language = /language-(\S+)/.exec(props.className ?? "")?.[1] ?? "";

  if (!("data-block" in props) || language === "mermaid") {
    return <defaultComponents.code {...props} />;
  }

  return (
    <FencedCode
      code={typeof props.children === "string" ? props.children : ""}
      language={language}
    />
  );
}

const REASONING_COMPONENTS: Components = { code: MarkdownCode };
const TEXT_COMPONENTS: Components = { code: MarkdownCode, cite: Citation, imgref: ImageRef };
const TEXT_ALLOWED_TAGS = { cite: ["src", "line"], imgref: ["src", "alt"] };

/** An assistant reply, with its citations and image references rendered inline. */
export function MarkdownText({ children }: { children: string }) {
  const normalized = useMemo(() => normalizeVoidTags(normalizeMath(children)), [children]);

  return (
    <Streamdown
      isAnimating={useMessageStreaming()}
      className={CLASS_NAME}
      allowedTags={TEXT_ALLOWED_TAGS}
      components={TEXT_COMPONENTS}
      plugins={STREAMDOWN_PLUGINS}
    >
      {normalized}
    </Streamdown>
  );
}

/** The model's reasoning, which cites nothing. */
export function ReasoningMarkdown({ children }: { children: string }) {
  const normalized = useMemo(() => normalizeDisplayMath(children), [children]);

  return (
    <Streamdown
      isAnimating={useMessageStreaming()}
      className={CLASS_NAME}
      components={REASONING_COMPONENTS}
      plugins={STREAMDOWN_PLUGINS}
    >
      {normalized}
    </Streamdown>
  );
}
