import {
  ArrowRightLeftIcon,
  FilePenIcon,
  FilePlusIcon,
  FileXIcon,
  FolderInputIcon,
  FolderPlusIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { TraceStep, TraceStepCollapsible } from "@/components/chat/TraceStep";
import { CodeBlock, CodeBlockContent, CodeBlockLine } from "@/components/ui/code-block";
import { Trace } from "@/components/ui/trace";
import { keyPrefix } from "@/i18n";
import { PathMoveSchema } from "@/lib/types";

const T_OPTIONS = keyPrefix(($) => $.chat.tools.changeset);

const FileDiffSchema = z.object({ path: z.string(), diff: z.string() });
type FileDiff = z.infer<typeof FileDiffSchema>;

/**
 * Mirrors `ChangesetSummary` in `backend/src/hivegent/changes.py`,
 * the metadata every approval of a workspace change asks with.
 */
export const ChangesetSummarySchema = z.object({
  creates: z.array(FileDiffSchema),
  updates: z.array(FileDiffSchema),
  moves: z.array(PathMoveSchema),
  deletes: z.array(z.string()),
  mkdirs: z.array(z.string()),
});
export type ChangesetSummary = z.infer<typeof ChangesetSummarySchema>;

/** How many changes *summary* lists, as `ChangesetSummary.count` counts them on the server. */
export function changeCount(summary: ChangesetSummary): number {
  return Object.values(summary).reduce((count, changes) => count + changes.length, 0);
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

/** Every change of one changeset as a line each: moves, new and changed files, deletions, then new folders. */
export function ChangesetView({ summary }: { summary: ChangesetSummary }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <Trace>
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
