/**
 * Generic agent-tool debugging console.
 *
 * Lists every agent tool exposed by the backend, renders a form for the
 * selected tool inferred from the JSON Schema of its Pydantic arguments, and
 * invokes it directly. The main use is exercising stateful behaviour that
 * unit tests don't cover, such as pgvector retrieval.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { keyPrefix } from "@/i18n";
import { formatDecimal } from "@/i18n/format";
import { runTool } from "@/lib/api";
import { prettyPrint } from "@/lib/chat/tool-part";
import type { ToolRunResult } from "@/lib/types";
import { errorMessage } from "@/lib/utils";
import { toolQueries } from "@/queries/tools";
import { EmptyState } from "@/components/EmptyState";
import { ErrorAlert } from "@/components/ErrorAlert";
import { HighlightedCode } from "@/components/HighlightedCode";
import { PageHeader } from "@/components/PageHeader";
import { isJsonSchema, type JsonSchema, SchemaForm } from "@/components/SchemaForm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";

const T_OPTIONS = keyPrefix(($) => $.app.toolDebugger);

/** Seed form values from the schema's declared defaults. */
function defaultValues(schema: JsonSchema): Record<string, unknown> {
  const out: Record<string, unknown> = {};

  for (const [key, prop] of Object.entries(schema.properties ?? {})) {
    if (prop.default !== undefined) out[key] = prop.default;
  }

  return out;
}

interface OutputProps {
  title: string;
  hint?: string;
  value: string;
  language?: string;
  variant?: "default" | "destructive";
}

/** One part of a tool's output, titled with what it is and who consumes it. */
function Output({ title, hint, value, language = "text", variant }: OutputProps) {
  return (
    <HighlightedCode
      code={value}
      language={language}
      title={title}
      actions={hint && <span className="truncate text-xs">{hint}</span>}
      wrap
      variant={variant}
      contentClassName="max-h-96"
    />
  );
}

function ResultPanel({ result }: { result: ToolRunResult }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const hasData = result.data !== null && result.data !== undefined;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {t(($) => $.result)}
          <Badge variant={result.ok ? "secondary" : "destructive"}>
            {result.ok ? t(($) => $.ok) : t(($) => $.error)}
          </Badge>
        </CardTitle>
        <CardAction className="text-xs text-muted-foreground">
          {t(($) => $.elapsed, { ms: formatDecimal(result.elapsed_ms) })}
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {result.error && (
          <Output title={t(($) => $.error)} value={result.error} variant="destructive" />
        )}
        {result.text && (
          <Output title={t(($) => $.llmText)} hint={t(($) => $.llmTextHint)} value={result.text} />
        )}
        {hasData && (
          <Output
            title={t(($) => $.structuredData)}
            hint={t(($) => $.structuredDataHint)}
            value={prettyPrint(result.data)}
            language="json"
          />
        )}
        {!result.error && !result.text && !hasData && (
          <p className="text-sm text-muted-foreground">{t(($) => $.noOutput)}</p>
        )}
      </CardContent>
    </Card>
  );
}

export function ToolDebugConsole() {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const { data: tools = [], error: loadError } = useQuery(toolQueries.schemas());
  const [selected, setSelected] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const run = useMutation({
    mutationFn: ({ name, args }: { name: string; args: Record<string, unknown> }) =>
      runTool(name, args),
    meta: { inlineError: true },
  });

  const selectedTool = useMemo(
    () => tools.find((tool) => tool.name === selected),
    [tools, selected],
  );

  const grouped = useMemo(() => [...Map.groupBy(tools, (tool) => tool.group)], [tools]);

  // Reset the form to the selected tool's schema defaults whenever it changes.
  function handleSelect(name: string | null) {
    const tool = tools.find((candidate) => candidate.name === name);
    setSelected(name);
    run.reset();
    setValues(tool && isJsonSchema(tool.parameters) ? defaultValues(tool.parameters) : {});
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={t(($) => $.title)}
        description={t(($) => $.description)}
        className="shrink-0 border-b px-6 py-4"
      />

      {/* Body: one scroll region on mobile, two independent columns on desktop. */}
      <div className="flex flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
        <div className="flex flex-col gap-6 p-6 lg:w-1/2 lg:overflow-y-auto lg:border-r">
          {loadError && (
            <ErrorAlert title={t(($) => $.loadFailed)}>{errorMessage(loadError)}</ErrorAlert>
          )}

          <Select value={selected} onValueChange={handleSelect}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder={t(($) => $.selectTool)} />
            </SelectTrigger>
            <SelectContent>
              {grouped.map(([group, groupTools]) => (
                <SelectGroup key={group}>
                  <SelectLabel className="capitalize">{group}</SelectLabel>
                  {groupTools.map((tool) => (
                    <SelectItem key={tool.name} value={tool.name}>
                      {tool.name}
                    </SelectItem>
                  ))}
                </SelectGroup>
              ))}
            </SelectContent>
          </Select>

          {selectedTool && (
            <Card>
              <CardHeader>
                <CardTitle className="font-mono">{selectedTool.name}</CardTitle>
                {selectedTool.description && (
                  <CardDescription>{selectedTool.description}</CardDescription>
                )}
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                {isJsonSchema(selectedTool.parameters) && (
                  <SchemaForm
                    schema={selectedTool.parameters}
                    values={values}
                    onChange={setValues}
                  />
                )}
                <Button
                  className="self-start"
                  onClick={() => run.mutate({ name: selectedTool.name, args: values })}
                  disabled={run.isPending}
                >
                  {run.isPending && <Spinner data-icon="inline-start" />}
                  {t(($) => $.run)}
                </Button>
              </CardContent>
            </Card>
          )}
        </div>

        <div className="flex flex-col gap-6 p-6 lg:w-1/2 lg:overflow-y-auto">
          {run.data ? (
            <ResultPanel result={run.data} />
          ) : run.error ? (
            <ErrorAlert title={t(($) => $.requestFailed)}>{errorMessage(run.error)}</ErrorAlert>
          ) : (
            <EmptyState
              title={t(($) => $.noResults)}
              description={t(($) => $.noResultsDescription)}
            />
          )}
        </div>
      </div>
    </div>
  );
}
