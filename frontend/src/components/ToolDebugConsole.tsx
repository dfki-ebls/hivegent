/**
 * Generic agent-tool debugging console.
 *
 * Lists every agent tool exposed by the backend, renders a form for the
 * selected tool inferred from the JSON Schema of its Pydantic arguments, and
 * invokes it directly. The main use is exercising stateful behaviour that
 * unit tests don't cover, such as pgvector retrieval. Admin only.
 */

import { type ReactNode, useEffect, useMemo, useState } from "react";
import { listToolSchemas, runTool } from "../lib/api";
import type { ToolRunResult, ToolSchema } from "../lib/types";
import { errorMessage } from "../lib/utils";
import { selectIsAdmin, useSettingsStore } from "../stores/settings-store";
import { type JsonSchema, SchemaForm } from "./SchemaForm";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "./ui/empty";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "./ui/select";
import { Spinner } from "./ui/spinner";

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
  const hasData = result.data !== null && result.data !== undefined;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          Result
          <Badge variant={result.ok ? "secondary" : "destructive"}>
            {result.ok ? "ok" : "error"}
          </Badge>
          <span className="ml-auto text-xs font-normal text-muted-foreground">
            {result.elapsed_ms.toFixed(1)} ms
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {result.error && (
          <Section title="Error">
            <pre className={`${PRE_CLASS} text-destructive`}>{result.error}</pre>
          </Section>
        )}
        {result.text && (
          <Section title="LLM text" hint="Stringified return value passed to the model">
            <pre className={PRE_CLASS}>{result.text}</pre>
          </Section>
        )}
        {hasData && (
          <Section title="Structured data" hint="Structured result the interface consumes">
            <pre className={PRE_CLASS}>{JSON.stringify(result.data, null, 2)}</pre>
          </Section>
        )}
        {!result.error && !result.text && !hasData && (
          <p className="text-sm text-muted-foreground">Tool returned no output.</p>
        )}
      </CardContent>
    </Card>
  );
}

export function ToolDebugConsole() {
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

  const selectedTool = useMemo(() => tools.find((t) => t.name === selected), [tools, selected]);

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
  useEffect(() => {
    setResult(null);
    setRunError(null);
    setValues(selectedTool ? defaultValues(selectedTool.parameters as unknown as JsonSchema) : {});
  }, [selectedTool]);

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
            <EmptyTitle>Administrator access required</EmptyTitle>
            <EmptyDescription>
              The tool debugger is only available to administrators.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 space-y-1 border-b px-6 py-4">
        <h1 className="text-2xl font-semibold">Tool Debugger</h1>
        <p className="text-sm text-muted-foreground">
          Invoke any agent tool directly to exercise stateful behaviour such as pgvector retrieval.
          Arguments and their types are inferred from each tool's schema.
        </p>
      </div>

      {/* Body: one scroll region on mobile, two independent columns on desktop. */}
      <div className="flex flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
        {/* Input column */}
        <div className="flex flex-col gap-6 p-6 lg:w-1/2 lg:overflow-y-auto lg:border-r">
          {loadError && (
            <p className="text-sm text-destructive">Failed to load tools: {loadError}</p>
          )}

          <div className="grid gap-1.5">
            <Select value={selected} onValueChange={setSelected}>
              <SelectTrigger>
                <SelectValue placeholder="Select a tool to debug" />
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
                <SchemaForm
                  schema={selectedTool.parameters as unknown as JsonSchema}
                  values={values}
                  onChange={setValues}
                />
                <Button className="self-start gap-2" onClick={handleRun} disabled={running}>
                  {running && <Spinner />}
                  Run tool
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
            <p className="text-sm text-destructive">Request failed: {runError}</p>
          ) : (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>No results yet</EmptyTitle>
                <EmptyDescription>
                  Select a tool, fill in its arguments, and run it to see the output here.
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}
        </div>
      </div>
    </div>
  );
}
