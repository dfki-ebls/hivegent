/**
 * Generic agent-tool debugging console.
 *
 * Lists every agent tool exposed by the backend, renders a form for the
 * selected tool inferred from the JSON Schema of its Pydantic arguments, and
 * invokes it directly. The main use is exercising stateful behaviour that
 * unit tests don't cover, such as pgvector retrieval. Admin only.
 */

import { type ReactNode, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { keyPrefix } from "@/i18n";
import { formatDecimal } from "@/i18n/format";
import { listToolSchemas, runTool } from "@/lib/api";
import type { ToolRunResult, ToolSchema } from "@/lib/types";
import { errorMessage } from "@/lib/utils";
import { selectIsAdmin, useSettingsStore } from "@/stores/settings-store";
import { isJsonSchema, type JsonSchema, SchemaForm } from "@/components/SchemaForm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
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

const PRE_CLASS =
  "max-h-96 overflow-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap break-words";

/** Seed form values from the schema's declared defaults. */
function defaultValues(schema: JsonSchema): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, prop] of Object.entries(schema.properties ?? {})) {
    if (prop.default !== undefined) out[key] = prop.default;
  }
  return out;
}

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <div className="grid gap-0.5">
        <span className="text-xs font-medium text-muted-foreground">{title}</span>
        {hint && <span className="text-[11px] text-muted-foreground/70">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

function ResultPanel({ result }: { result: ToolRunResult }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const hasData = result.data !== null && result.data !== undefined;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          {t(($) => $.result)}
          <Badge variant={result.ok ? "secondary" : "destructive"}>
            {result.ok ? t(($) => $.ok) : t(($) => $.error)}
          </Badge>
          <span className="ml-auto text-xs font-normal text-muted-foreground">
            {t(($) => $.elapsed, {
              ms: formatDecimal(result.elapsed_ms),
            })}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {result.error && (
          <Section title={t(($) => $.error)}>
            <pre className={`${PRE_CLASS} text-destructive`}>{result.error}</pre>
          </Section>
        )}
        {result.text && (
          <Section title={t(($) => $.llmText)} hint={t(($) => $.llmTextHint)}>
            <pre className={PRE_CLASS}>{result.text}</pre>
          </Section>
        )}
        {hasData && (
          <Section title={t(($) => $.structuredData)} hint={t(($) => $.structuredDataHint)}>
            <pre className={PRE_CLASS}>{JSON.stringify(result.data, null, 2)}</pre>
          </Section>
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
  const isAdmin = useSettingsStore(selectIsAdmin);
  const [tools, setTools] = useState<ToolSchema[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState("");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ToolRunResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  useEffect(() => {
    if (!isAdmin) return;

    let active = true;
    listToolSchemas()
      .then((fetched) => active && setTools(fetched))
      .catch((e: unknown) => {
        if (active) setLoadError(errorMessage(e));
      });

    return () => {
      active = false;
    };
  }, [isAdmin]);

  const selectedTool = useMemo(
    () => tools.find((tool) => tool.name === selected),
    [tools, selected],
  );

  const grouped = useMemo(() => {
    const map = new Map<string, ToolSchema[]>();
    for (const tool of tools) {
      const list = map.get(tool.group) ?? [];
      list.push(tool);
      map.set(tool.group, list);
    }
    return [...map.entries()];
  }, [tools]);

  // Reset the form to the selected tool's schema defaults whenever it changes.
  function handleSelect(name: string) {
    const tool = tools.find((candidate) => candidate.name === name);
    setSelected(name);
    setResult(null);
    setRunError(null);
    setValues(tool && isJsonSchema(tool.parameters) ? defaultValues(tool.parameters) : {});
  }

  async function handleRun() {
    if (!selectedTool) return;
    setRunning(true);
    setResult(null);
    setRunError(null);
    try {
      setResult(await runTool(selectedTool.name, values));
    } catch (e: unknown) {
      setRunError(errorMessage(e));
    } finally {
      setRunning(false);
    }
  }

  if (!isAdmin) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <Empty>
          <EmptyHeader>
            <EmptyTitle>{t(($) => $.adminRequired)}</EmptyTitle>
            <EmptyDescription>{t(($) => $.adminRequiredDescription)}</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 space-y-1 border-b px-6 py-4">
        <h1 className="text-2xl font-semibold">{t(($) => $.title)}</h1>
        <p className="text-sm text-muted-foreground">{t(($) => $.description)}</p>
      </div>

      {/* Body: one scroll region on mobile, two independent columns on desktop. */}
      <div className="flex flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
        {/* Input column */}
        <div className="flex flex-col gap-6 p-6 lg:w-1/2 lg:overflow-y-auto lg:border-r">
          {loadError && (
            <p className="text-sm text-destructive">
              {t(($) => $.loadFailed, { error: loadError })}
            </p>
          )}

          <div className="grid gap-1.5">
            <Select value={selected} onValueChange={handleSelect}>
              <SelectTrigger>
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
          </div>

          {selectedTool && (
            <Card>
              <CardHeader>
                <CardTitle className="font-mono text-base">{selectedTool.name}</CardTitle>
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
                <Button className="self-start gap-2" onClick={handleRun} disabled={running}>
                  {running && <Spinner />}
                  {t(($) => $.run)}
                </Button>
              </CardContent>
            </Card>
          )}
        </div>

        {/* Output column */}
        <div className="flex flex-col gap-6 p-6 lg:w-1/2 lg:overflow-y-auto">
          {result ? (
            <ResultPanel result={result} />
          ) : runError ? (
            <p className="text-sm text-destructive">
              {t(($) => $.requestFailed, { error: runError })}
            </p>
          ) : (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>{t(($) => $.noResults)}</EmptyTitle>
                <EmptyDescription>{t(($) => $.noResultsDescription)}</EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}
        </div>
      </div>
    </div>
  );
}
