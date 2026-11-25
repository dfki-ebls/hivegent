import { useTranslation } from "react-i18next";

import { type CaseBase } from "@/api/case-bases";
import { Section } from "@/components/documents/similarity/section";
import { Badge } from "@/components/ui/badge";
import { CodeBlock, CodeBlockContent } from "@/components/ui/code-block";
import { humanize } from "@/lib/utils";
import {
  type Json,
  type JsonObject,
  elementLabel,
  isJsonObject,
  valueAt,
  weightAt,
} from "@/lib/similarity";

/** The configuration a case base is ranked by, inferred from its data and never saved. */
export function ConfigView({ caseBase }: { caseBase: CaseBase }) {
  return (
    <>
      <DecisionsSection caseBase={caseBase} />
      {PHASES.map((name) => (
        <PhaseSection key={name} name={name} value={caseBase.config[name]} />
      ))}
    </>
  );
}

/** The decisions behind the measure of the configuration, each with its measure and weight. */
function DecisionsSection({ caseBase }: { caseBase: CaseBase }) {
  const { t } = useTranslation();
  const { config } = caseBase;
  const measure = config.measure ?? null;
  const decisions = isJsonObject(config.provenance) ? config.provenance : {};

  return (
    <Section
      title={t(($) => $.documents.similarity.decisions)}
      hint={t(($) => $.documents.similarity.decisionsHint)}
    >
      <p className="text-sm">
        {t(($) => $.documents.similarity.summary, {
          count: caseBase.count,
          kind: caseBase.graphs
            ? t(($) => $.documents.similarity.graphs)
            : t(($) => $.documents.similarity.records),
        })}
      </p>
      <p className="text-sm text-muted-foreground">{t(($) => $.documents.similarity.inferred)}</p>
      <ul className="divide-y rounded-lg border text-sm">
        {Object.entries(decisions).map(([pointer, decision]) => (
          <DecisionRow
            key={pointer}
            pointer={pointer}
            decision={isJsonObject(decision) ? decision : {}}
            measure={measure}
          />
        ))}
      </ul>
    </Section>
  );
}

/** The phases of the CBR cycle and the maintenance, each shown with its settings or as off. */
const PHASES = ["retrieve", "reuse", "revise", "retain", "maintain"] as const;

function PhaseSection({ name, value }: { name: (typeof PHASES)[number]; value: Json | undefined }) {
  const { t } = useTranslation();

  return (
    <Section
      title={t(($) => $.documents.similarity.sections[name].title)}
      hint={t(($) => $.documents.similarity.sections[name].hint)}
    >
      {isJsonObject(value) ? (
        <CodeBlock size="sm">
          <CodeBlockContent wrap>{JSON.stringify(value, null, 2)}</CodeBlockContent>
        </CodeBlock>
      ) : (
        <p className="text-sm text-muted-foreground">{t(($) => $.documents.similarity.off)}</p>
      )}
    </Section>
  );
}

function DecisionRow({
  pointer,
  decision,
  measure,
}: {
  pointer: string;
  decision: JsonObject;
  measure: Json;
}) {
  const { t } = useTranslation();
  const current = valueAt(measure, pointer);
  const kind = isJsonObject(current) && typeof current.kind === "string" ? current.kind : "";
  const weight = weightAt(measure, pointer);

  return (
    <li className="flex flex-col gap-2 p-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium">
          {elementLabel(pointer) || t(($) => $.documents.similarity.whole)}
        </span>
        {decision.absent === true && (
          <Badge variant="outline">{t(($) => $.documents.similarity.absent)}</Badge>
        )}
        {typeof decision.reason === "string" && decision.reason && (
          <span className="text-xs text-muted-foreground">{decision.reason}</span>
        )}
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-muted-foreground">{t(($) => $.documents.similarity.measure)}</dt>
        <dd>{humanize(kind)}</dd>
        {weight !== null && (
          <>
            <dt className="text-muted-foreground">{t(($) => $.documents.similarity.weight)}</dt>
            <dd>{weight}</dd>
          </>
        )}
      </dl>
    </li>
  );
}
