import {
  ArrowRightLeftIcon,
  CircleMinusIcon,
  CirclePlusIcon,
  FilePenIcon,
  FilePlusIcon,
  FileXIcon,
  FolderInputIcon,
  FolderPlusIcon,
  RefreshCwIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { TraceStep, TraceStepCollapsible } from "@/components/chat/trace-step";
import { CodeBlock, CodeBlockContent, CodeBlockLine } from "@/components/ui/code-block";
import { Trace } from "@/components/ui/trace";
import { keyPrefix } from "@/i18n";
import { PathMoveSchema } from "@/api/jobs";

const T_OPTIONS = keyPrefix(($) => $.chat.tools.changeset);

const FileDiffSchema = z.object({ path: z.string(), diff: z.string() });
type FileDiff = z.infer<typeof FileDiffSchema>;

/** Mirrors `ChangeReason`, why a planner such as cbrkit's retain changes one case. */
const ChangeReasonSchema = z.object({
  action: z.enum(["add", "update", "remove"]),
  key: z.union([z.string(), z.number()]).nullable(),
  reason: z.string(),
});
type ChangeReason = z.infer<typeof ChangeReasonSchema>;

const REASON_ICONS = {
  add: <CirclePlusIcon />,
  update: <RefreshCwIcon />,
  remove: <CircleMinusIcon />,
} as const;

/**
 * Mirrors `ChangesetSummary` in `backend/src/hivegent/tools/changes.py`,
 * the metadata every approval of a workspace change asks with.
 */
export const ChangesetSummarySchema = z.object({
  creates: z.array(FileDiffSchema),
  updates: z.array(FileDiffSchema),
  moves: z.array(PathMoveSchema),
  deletes: z.array(z.string()),
  mkdirs: z.array(z.string()),
  reasons: z.array(ChangeReasonSchema).default([]),
});
export type ChangesetSummary = z.infer<typeof ChangesetSummarySchema>;

/** How many changes *summary* lists, as `ChangesetSummary.count` counts them on the server. */
export function changeCount({
  creates,
  updates,
  moves,
  deletes,
  mkdirs,
}: ChangesetSummary): number {
  return [creates, updates, moves, deletes, mkdirs].reduce(
    (count, changes) => count + changes.length,
    0,
  );
}

/** Why the planner of a changeset changes one case, its reason below the change. */
function ReasonStep({ change }: { change: ChangeReason }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const { action, key } = change;

  return (
    <TraceStep
      icon={REASON_ICONS[action]}
      title={key === null ? t(($) => $.reasons.addNew) : t(($) => $.reasons[action], { key })}
      description={change.reason}
    />
  );
}

/** The hunks of a unified diff, without the file headers above the first one. */
function hunkLines(diff: string): string[] {
  const lines = diff.replace(/\n$/, "").split("\n");
  const start = lines.findIndex((line) => line.startsWith("@@"));

  return start === -1 ? lines : lines.slice(start);
}

function DiffLines({ lines }: { lines: string[] }) {
  return lines.map((line, index) =>
    line.startsWith("@@") ? (
      <CodeBlockLine key={index} className="text-muted-foreground">
        {line}
      </CodeBlockLine>
    ) : (
      <CodeBlockLine
        key={index}
        variant={line.startsWith("+") ? "added" : line.startsWith("-") ? "removed" : "default"}
      >
        {line.slice(1)}
      </CodeBlockLine>
    ),
  );
}

/** A created or changed file, which opens to its diff once the diff is known. */
function FileChange({ file, created }: { file: FileDiff; created: boolean }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const lines = hunkLines(file.diff);
  const additions = lines.filter((line) => line.startsWith("+")).length;
  const deletions = lines.filter((line) => line.startsWith("-")).length;
  const path = file.path;

  return (
    <TraceStepCollapsible
      icon={created ? <FilePlusIcon /> : <FilePenIcon />}
      title={created ? t(($) => $.created, { path }) : t(($) => $.updated, { path })}
      description={file.diff ? `+${additions} −${deletions}` : undefined}
    >
      {file.diff && (
        <CodeBlock size="sm">
          <CodeBlockContent className="max-h-80 scroll-fade-y">
            <DiffLines lines={lines} />
          </CodeBlockContent>
        </CodeBlock>
      )}
    </TraceStepCollapsible>
  );
}

/**
 * Every change of one changeset as a line each: the planner's reasons, moves,
 * new and changed files, deletions, then new folders.
 */
export function ChangesetView({ summary }: { summary: ChangesetSummary }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <Trace>
      {summary.reasons.map((change, index) => (
        <ReasonStep key={index} change={change} />
      ))}
      {summary.moves.map((move) => (
        <TraceStep
          key={move.source}
          icon={move.is_dir ? <FolderInputIcon /> : <ArrowRightLeftIcon />}
          title={t(($) => $.moved, { source: move.source, destination: move.destination })}
          description={move.replaces ? t(($) => $.replaces) : undefined}
        />
      ))}
      {summary.creates.map((file) => (
        <FileChange key={file.path} file={file} created />
      ))}
      {summary.updates.map((file) => (
        <FileChange key={file.path} file={file} created={false} />
      ))}
      {summary.deletes.map((path) => (
        <TraceStep key={path} icon={<FileXIcon />} title={t(($) => $.deleted, { path })} />
      ))}
      {summary.mkdirs.map((path) => (
        <TraceStep key={path} icon={<FolderPlusIcon />} title={t(($) => $.mkdir, { path })} />
      ))}
    </Trace>
  );
}
