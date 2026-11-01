import type { ComponentProps, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  CodeBlock,
  CodeBlockActions,
  CodeBlockContent,
  CodeBlockCopyButton,
  CodeBlockHeader,
  CodeBlockLine,
  CodeBlockTitle,
} from "@/components/ui/code-block";
import { useHighlightedCode } from "@/hooks/use-highlighted-code";

interface HighlightedCodeProps extends Pick<
  ComponentProps<typeof CodeBlock>,
  "size" | "variant" | "className"
> {
  code: string;
  language: string;
  title?: ReactNode;
  /** Shown at the end of the header, such as a badge. */
  actions?: ReactNode;
  copyable?: boolean;
  lineNumbers?: boolean;
  wrap?: boolean;
  contentClassName?: string;
}

/** A syntax-highlighted code block, with an optional header holding a title, actions, and a copy button. */
export function HighlightedCode({
  code,
  language,
  title,
  actions,
  copyable = false,
  lineNumbers = false,
  wrap = false,
  size = "sm",
  variant,
  className,
  contentClassName,
}: HighlightedCodeProps) {
  const { t } = useTranslation();
  const lines = useHighlightedCode(code, language);

  return (
    <CodeBlock size={size} variant={variant} className={className}>
      {(title || actions || copyable) && (
        <CodeBlockHeader>
          {title && <CodeBlockTitle>{title}</CodeBlockTitle>}
          {(actions || copyable) && (
            <CodeBlockActions>
              {actions}
              {copyable && (
                <CodeBlockCopyButton value={code} aria-label={t(($) => $.common.actions.copy)} />
              )}
            </CodeBlockActions>
          )}
        </CodeBlockHeader>
      )}
      <CodeBlockContent lineNumbers={lineNumbers} wrap={wrap} className={contentClassName}>
        {lines.map((line, index) => (
          <CodeBlockLine key={index}>{line}</CodeBlockLine>
        ))}
      </CodeBlockContent>
    </CodeBlock>
  );
}
