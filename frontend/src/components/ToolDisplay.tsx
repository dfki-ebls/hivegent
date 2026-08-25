import type { ReactNode } from "react";
import { parseJson } from "@/lib/chat/tool-part";
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
  className?: string;
}

/** Pre-formatted block for values that carry their own line breaks. */
export function ToolPre({ children, className }: ToolPreProps) {
  return (
    <pre className={cn("whitespace-pre-wrap break-words font-mono text-xs", className)}>
      {children}
    </pre>
  );
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
  if (!params || Object.keys(params).length === 0) {
    return null;
  }

  return (
    <ToolSection title="Parameters">
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
  return (
    <ToolSection title="Result" border>
      {children}
    </ToolSection>
  );
}

interface ToolErrorProps {
  message: string;
}

export function ToolError({ message }: ToolErrorProps) {
  return (
    <ToolSection title="Error" variant="error" border>
      <ToolPre>{message}</ToolPre>
    </ToolSection>
  );
}
