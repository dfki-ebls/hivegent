import type { ReactNode } from "react";
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

interface ToolKeyValueProps {
  label: string;
  value: ReactNode;
  indent?: boolean;
}

export function ToolKeyValue({ label, value, indent = false }: ToolKeyValueProps) {
  return (
    <div className={cn(indent && "pl-4")}>
      <span className="text-muted-foreground">{label}:</span>{" "}
      <span className="font-medium">{value}</span>
    </div>
  );
}

interface ToolParametersProps {
  params: Record<string, unknown>;
}

function formatValue(value: unknown): ReactNode {
  if (typeof value === "string") {
    // Try parsing as JSON for pretty-printing
    try {
      const parsed = JSON.parse(value);
      if (typeof parsed === "object" && parsed !== null) {
        return (
          <pre className="mt-1 whitespace-pre-wrap text-xs font-mono">
            {JSON.stringify(parsed, null, 2)}
          </pre>
        );
      }
    } catch {
      // Not JSON, display as quoted string
    }
    return `"${value}"`;
  }
  if (typeof value === "object" && value !== null) {
    return (
      <pre className="mt-1 whitespace-pre-wrap text-xs font-mono">
        {JSON.stringify(value, null, 2)}
      </pre>
    );
  }
  return String(value);
}

export function ToolParameters({ params }: ToolParametersProps) {
  if (!params || Object.keys(params).length === 0) {
    return null;
  }

  return (
    <ToolSection title="Parameters">
      {Object.entries(params).map(([key, value]) => (
        <ToolKeyValue key={key} label={key} value={formatValue(value)} />
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
      {message}
    </ToolSection>
  );
}
