import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { parseJson, prettyPrint, type ToolPart } from "@/lib/chat/tool-part";
import { cn } from "@/lib/utils";

interface ToolSectionProps {
  title: string;
  variant?: "default" | "error";
  border?: boolean;
  children: ReactNode;
}

export function ToolSection({
  title,
  variant = "default",
  border = false,
  children,
}: ToolSectionProps) {
  return (
    <div className={cn("space-y-2 p-4", border && "border-t")}>
      <h4
        className={cn(
          "font-medium text-xs uppercase tracking-wide",
          variant === "error" ? "text-destructive" : "text-muted-foreground",
        )}
      >
        {title}
      </h4>
      <div className={cn("text-sm space-y-1", variant === "error" && "text-destructive")}>
        {children}
      </div>
    </div>
  );
}

interface ToolPreProps {
  children: string;
  /** Set the block off in a scrolling box, for long output or content. */
  boxed?: boolean;
  className?: string;
}

/** Pre-formatted block for values that carry their own line breaks. */
export function ToolPre({ children, boxed = false, className }: ToolPreProps) {
  return (
    <pre
      className={cn(
        "whitespace-pre-wrap break-words font-mono text-xs",
        boxed && "max-h-80 overflow-auto rounded-md bg-muted/40 p-2",
        className,
      )}
    >
      {children}
    </pre>
  );
}

/** A workspace path or other server value set inline in prose. */
export function PathCode({ children }: { children?: ReactNode }) {
  return <code className="break-all font-mono text-xs">{children}</code>;
}

/**
 * Text for values that need a block of their own: objects, JSON-encoded
 * strings, and any multi-line string such as an edit's ``old_string``.
 * ``null`` for scalars, which stay inline next to their label.
 */
function blockText(value: unknown): string | null {
  const parsed = typeof value === "string" ? parseJson<unknown>(value) : value;
  if (typeof parsed === "object" && parsed !== null) {
    return JSON.stringify(parsed, null, 2);
  }

  return typeof value === "string" && value.includes("\n") ? value : null;
}

interface ToolParameterProps {
  label: string;
  value: unknown;
}

function ToolParameter({ label, value }: ToolParameterProps) {
  const block = blockText(value);
  const inline = typeof value === "string" ? `"${value}"` : String(value);

  return (
    <div>
      <span className="text-muted-foreground">{label}:</span>
      {block === null ? (
        <span className="font-medium"> {inline}</span>
      ) : (
        <ToolPre className="mt-1">{block}</ToolPre>
      )}
    </div>
  );
}

interface ToolParametersProps {
  params: Record<string, unknown>;
}

export function ToolParameters({ params }: ToolParametersProps) {
  const { t } = useTranslation();

  if (!params || Object.keys(params).length === 0) {
    return null;
  }

  return (
    <ToolSection title={t(($) => $.chat.tools.sections.parameters)}>
      {Object.entries(params).map(([key, value]) => (
        <ToolParameter key={key} label={key} value={value} />
      ))}
    </ToolSection>
  );
}

interface ToolResultProps {
  children: ReactNode;
}

export function ToolResult({ children }: ToolResultProps) {
  const { t } = useTranslation();

  return (
    <ToolSection title={t(($) => $.chat.tools.sections.result)} border>
      {children}
    </ToolSection>
  );
}

interface ToolOutputResultProps {
  part: ToolPart;
  /** The output's text form, in place of pretty-printing the raw output. */
  formatted?: string | null;
}

/**
 * The call's raw output, for tools without a structured view of it.
 *
 * A list output is a content list, such as a result followed by the user's
 * approval note, so each item gets its own block.
 */
export function ToolOutputResult({ part, formatted }: ToolOutputResultProps) {
  if (part.output === undefined) return null;

  const items: unknown[] = Array.isArray(part.output) ? part.output : [part.output];

  return (
    <ToolResult>
      {formatted != null ? (
        <ToolPre>{formatted}</ToolPre>
      ) : (
        items.map((item, index) => <ToolPre key={index}>{prettyPrint(item)}</ToolPre>)
      )}
    </ToolResult>
  );
}

interface ToolErrorProps {
  message: string;
}

export function ToolError({ message }: ToolErrorProps) {
  const { t } = useTranslation();

  return (
    <ToolSection title={t(($) => $.common.states.error)} variant="error" border>
      <ToolPre>{message}</ToolPre>
    </ToolSection>
  );
}
