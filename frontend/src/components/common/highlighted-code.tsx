import { type ComponentProps, type ReactNode, useMemo } from "react";
import { CopyButton } from "@/components/common/copy-button";
import {
  CodeBlock,
  CodeBlockActions,
  CodeBlockContent,
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
  const lines = useHighlightedCode(code, language);

  return (
    <CodeBlock size={size} variant={variant} className={className}>
      {(title || actions || copyable) && (
        <CodeBlockHeader>
          {title && <CodeBlockTitle>{title}</CodeBlockTitle>}
          {(actions || copyable) && (
            <CodeBlockActions>
              {actions}
              {copyable && <CopyButton value={code} />}
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

interface ValueCodeProps extends Omit<HighlightedCodeProps, "code" | "language"> {
  value: unknown;
}

/** A value as a code block, a string as plain text and anything else as JSON. */
export function ValueCode({ value, ...props }: ValueCodeProps) {
  const isText = typeof value === "string";
  const code = useMemo(
    () => (isText ? value : (JSON.stringify(value, null, 2) ?? String(value))),
    [isText, value],
  );

  return <HighlightedCode code={code} language={isText ? "text" : "json"} {...props} />;
}
