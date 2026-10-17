import { type ComponentProps, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { HighlightedCode } from "@/components/HighlightedCode";
import { parseJson, type ToolPart } from "@/lib/chat/tool-part";

type ToolCodeProps = Pick<
  ComponentProps<typeof HighlightedCode>,
  "code" | "title" | "actions" | "copyable" | "variant"
> & { language?: string };

/** What a call took or gave, as a small code block that wraps and scrolls. */
export function ToolCode({ language = "text", ...props }: ToolCodeProps) {
  return <HighlightedCode language={language} wrap contentClassName="max-h-64" {...props} />;
}

/** A value that is or holds JSON, parsed once into its pretty text and the language that highlights it. */
function codeOf(value: unknown): { code: string; language: string } {
  const parsed = typeof value === "string" ? parseJson<unknown>(value) : value;

  if (typeof parsed === "object" && parsed !== null) {
    return { code: JSON.stringify(parsed, null, 2), language: "json" };
  }

  return { code: typeof value === "string" ? value : String(value), language: "text" };
}

/** A value as YAML, with objects as JSON flow collections and multi-line strings as block scalars. */
function yamlValue(value: unknown): string {
  const { code, language } = codeOf(value);

  if (language === "json") return `\n${code}`.replaceAll("\n", "\n  ");

  if (typeof value === "string" && value.includes("\n")) {
    return ` |\n${value}`.replaceAll("\n", "\n  ");
  }

  return ` ${JSON.stringify(value) ?? String(value)}`;
}

/** The arguments of a call, one per line. */
export function ToolParameters({ params }: { params: Record<string, unknown> }) {
  const { t } = useTranslation();
  const code = useMemo(
    () =>
      Object.entries(params)
        .map(([key, value]) => `${key}:${yamlValue(value)}`)
        .join("\n"),
    [params],
  );

  if (!code) return null;

  return (
    <ToolCode title={t(($) => $.chat.tools.sections.parameters)} code={code} language="yaml" />
  );
}

/**
 * The call's raw output, for tools without a structured view of it.
 * A list output is a content list, such as a result followed by the user's approval note,
 * so each item gets its own block.
 */
export function ToolOutputResult({ part }: { part: ToolPart }) {
  const { t } = useTranslation();
  const blocks = useMemo(
    () =>
      part.output === undefined
        ? []
        : (Array.isArray(part.output) ? part.output : [part.output]).map(codeOf),
    [part.output],
  );

  return blocks.map(({ code, language }, index) => (
    <ToolCode
      key={index}
      title={t(($) => $.chat.tools.sections.result)}
      code={code}
      language={language}
    />
  ));
}

export function ToolError({ message }: { message: string }) {
  const { t } = useTranslation();

  return <ToolCode title={t(($) => $.common.states.error)} code={message} variant="destructive" />;
}
