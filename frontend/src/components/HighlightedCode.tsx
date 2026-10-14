import type { ReactNode } from "react";
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

interface HighlightedCodeProps {
  code: string;
  language: string;
  title?: ReactNode;
  copyable?: boolean;
  lineNumbers?: boolean;
  className?: string;
}

/** A syntax-highlighted code block, with an optional title and copy button. */
export function HighlightedCode({
  code,
  language,
  title,
  copyable = false,
  lineNumbers = false,
  className,
}: HighlightedCodeProps) {
  const { t } = useTranslation();
  const lines = useHighlightedCode(code, language);

  return (
    <CodeBlock size="sm" className={className}>
      {(title || copyable) && (
        <CodeBlockHeader>
          {title && <CodeBlockTitle>{title}</CodeBlockTitle>}
          {copyable && (
            <CodeBlockActions>
              <CodeBlockCopyButton value={code} aria-label={t(($) => $.common.actions.copy)} />
            </CodeBlockActions>
          )}
        </CodeBlockHeader>
      )}
      <CodeBlockContent lineNumbers={lineNumbers}>
        {lines.map((line, index) => (
          <CodeBlockLine key={index}>{line}</CodeBlockLine>
        ))}
      </CodeBlockContent>
    </CodeBlock>
  );
}
