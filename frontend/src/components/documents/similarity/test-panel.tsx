import { useMutation } from "@tanstack/react-query";
import { Suspense, lazy, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type CaseBase,
  type CaseMatch,
  type CaseQuery,
  type Explanation,
  caseBaseMutations,
} from "@/api/case-bases";
import { PendingIcon, Spinner } from "@/components/common/spinner";
import { SchemaForm, isJsonSchema } from "@/components/schema-form";
import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatPercent, formatScore } from "@/i18n/format";
import { errorMessage, humanize } from "@/lib/utils";
import { type Json, jsonObject, jsonText, mappingDiagram } from "@/lib/similarity";
import { Section } from "@/components/documents/similarity/section";

export function TestPanel({ caseBase }: { caseBase: CaseBase }) {
  const { t } = useTranslation();
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [key, setKey] = useState("");
  const [graphPath, setGraphPath] = useState("");
  const run = useMutation({ ...caseBaseMutations.query(), meta: { inlineError: true } });
  const queryForm = isJsonSchema(caseBase.query_schema) ? caseBase.query_schema : null;

  const submit = () => {
    // cbrkit ignores an attribute left out, but an empty one would count.
    const filled = Object.fromEntries(
      Object.entries(values).filter(([, value]) => value != null && value !== ""),
    );
    const query: CaseQuery = key
      ? { query_key: key, limit: 5 }
      : graphPath
        ? { query_path: graphPath, limit: 5 }
        : { query: jsonObject(filled) ?? {}, limit: 5 };
    run.mutate({ path: caseBase.path, query });
  };

  return (
    <Section
      title={t(($) => $.documents.similarity.test)}
      hint={t(($) => $.documents.similarity.testHint)}
    >
      {!caseBase.graphs && queryForm && (
        <SchemaForm
          schema={queryForm}
          values={values}
          onChange={setValues}
          data
          className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))]"
        />
      )}
      <div className="grid gap-2 sm:grid-cols-2">
        <Field>
          <FieldLabel>{t(($) => $.documents.similarity.caseKey)}</FieldLabel>
          <Input value={key} onChange={(e) => setKey(e.target.value)} />
        </Field>
        {caseBase.graphs && (
          <Field>
            <FieldLabel>{t(($) => $.documents.similarity.graphPath)}</FieldLabel>
            <Input value={graphPath} onChange={(e) => setGraphPath(e.target.value)} />
          </Field>
        )}
      </div>
      <div>
        <Button onClick={submit} disabled={run.isPending}>
          <PendingIcon pending={run.isPending} />
          {t(($) => $.documents.similarity.run)}
        </Button>
      </div>
      {run.error && <FieldError>{errorMessage(run.error)}</FieldError>}
      {run.data &&
        (run.data.matches.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t(($) => $.documents.similarity.noMatches)}
          </p>
        ) : (
          <ol className="flex flex-col gap-4">
            {run.data.matches.map((match) => (
              <MatchCard key={jsonText(match.key)} match={match} query={run.data.query} />
            ))}
          </ol>
        ))}
    </Section>
  );
}

/** Streamdown is the chat's, so a graph match loads it only when it shows a diagram. */
const MermaidDiagram = lazy(() => import("@/components/documents/mermaid-diagram"));

/** A value of a match as text, a muted dash where it is none. */
function ValueCell({ value }: { value: Json | undefined }) {
  const text = jsonText(value);

  return (
    <TableCell className="max-w-48 truncate">
      {text || <span className="text-muted-foreground">-</span>}
    </TableCell>
  );
}

function SimilarityBar({ value }: { value: number }) {
  return (
    <span className="flex items-center gap-2">
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
        <span
          className="block h-full bg-primary"
          style={{ width: `${Math.round(value * 100)}%` }}
        />
      </span>
      <span className="tabular-nums">{formatScore(value)}</span>
    </span>
  );
}

function attributeRows(
  explanation: Explanation,
  depth = 0,
): { name: string; depth: number; item: Explanation }[] {
  return Object.entries(explanation.attributes).flatMap(([name, item]) => [
    { name, depth, item },
    ...attributeRows(item, depth + 1),
  ]);
}

function MatchCard({ match, query }: { match: CaseMatch; query: Json }) {
  const { t } = useTranslation();
  const graph = match.explanation.graph;
  const rows = attributeRows(match.explanation);
  const diagram = graph ? mappingDiagram(match.value, query, graph.nodes) : null;

  return (
    <li className="flex flex-col gap-2 rounded-lg border p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{jsonText(match.key)}</span>
        <SimilarityBar value={match.score} />
      </div>
      {rows.length > 0 && (
        <Table className="text-xs">
          <TableHeader>
            <TableRow>
              <TableHead>{t(($) => $.documents.similarity.element)}</TableHead>
              <TableHead>{t(($) => $.documents.similarity.query)}</TableHead>
              <TableHead>{t(($) => $.documents.similarity.case)}</TableHead>
              <TableHead>{t(($) => $.documents.similarity.weight)}</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(({ name, depth, item }) => (
              <TableRow key={`${depth}-${name}`}>
                <TableCell style={{ paddingLeft: `${0.5 + depth}rem` }}>{humanize(name)}</TableCell>
                <ValueCell value={item.query} />
                <ValueCell value={item.case} />
                <TableCell>{item.weight ?? ""}</TableCell>
                <TableCell>
                  <SimilarityBar value={item.similarity} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {graph && (
        <div className="flex flex-col gap-2 text-sm">
          <p className="text-muted-foreground">
            {t(($) => $.documents.similarity.coverage, {
              nodes: formatPercent(graph.node_coverage),
              edges: formatPercent(graph.edge_coverage),
            })}
            {!graph.exact && ` (${t(($) => $.documents.similarity.approximate)})`}
          </p>
          {graph.unmapped_nodes.length > 0 && (
            <p className="text-muted-foreground">
              {t(($) => $.documents.similarity.unmapped, {
                nodes: graph.unmapped_nodes.map(jsonText).join(", "),
              })}
            </p>
          )}
          {diagram && (
            <Suspense fallback={<Spinner aria-hidden />}>
              <MermaidDiagram source={diagram} />
            </Suspense>
          )}
        </div>
      )}
    </li>
  );
}
