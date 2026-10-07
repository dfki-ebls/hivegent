import { ArrowRightIcon, FolderIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { CodeBlock, CodeBlockHeader, CodeBlockTitle } from "@/components/ai-elements/code-block";
import { PathCode } from "@/components/ToolDisplay";
import { Badge } from "@/components/ui/badge";
import { keyPrefix } from "@/i18n";

const T_OPTIONS = keyPrefix(($) => $.chat.tools.changeset);

const FileDiffSchema = z.object({ path: z.string(), diff: z.string() });

const PathMoveSchema = z.object({
  source: z.string(),
  destination: z.string(),
  is_dir: z.boolean(),
  replaces: z.boolean(),
});
type PathMove = z.infer<typeof PathMoveSchema>;

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
  return (
    summary.creates.length +
    summary.updates.length +
    summary.moves.length +
    summary.deletes.length +
    summary.mkdirs.length
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <p className="text-muted-foreground text-xs">{title}</p>
      {children}
    </div>
  );
}

/** Each file with its unified diff, or its path alone when the diff is not known yet. */
function Diffs({ files }: { files: ChangesetSummary["creates"] }) {
  return files.map((file) =>
    file.diff ? (
      <CodeBlock key={file.path} code={file.diff} language="diff">
        <CodeBlockHeader>
          <CodeBlockTitle>
            <PathCode>{file.path}</PathCode>
          </CodeBlockTitle>
        </CodeBlockHeader>
      </CodeBlock>
    ) : (
      <p key={file.path}>
        <PathCode>{file.path}</PathCode>
      </p>
    ),
  );
}

function PathList({ paths }: { paths: readonly string[] }) {
  return (
    <ul className="space-y-1">
      {paths.map((path) => (
        <li key={path}>
          <PathCode>{path}</PathCode>
        </li>
      ))}
    </ul>
  );
}

function MoveRow({ move }: { move: PathMove }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <li className="flex flex-wrap items-center gap-x-2">
      {move.is_dir && (
        <>
          <FolderIcon aria-hidden className="size-3 shrink-0 text-muted-foreground" />
          <span className="sr-only">{t(($) => $.folder)}</span>
        </>
      )}
      <PathCode>{move.source}</PathCode>
      <ArrowRightIcon aria-hidden className="size-3 shrink-0 text-muted-foreground" />
      <PathCode>{move.destination}</PathCode>
      {move.replaces && <Badge variant="secondary">{t(($) => $.replaces)}</Badge>}
    </li>
  );
}

function MoveList({ moves }: { moves: readonly PathMove[] }) {
  return (
    <ul className="space-y-1">
      {moves.map((move) => (
        <MoveRow key={move.source} move={move} />
      ))}
    </ul>
  );
}

/** Every change of one changeset: moves, new and changed files with their diffs, deletions, then new folders. */
export function ChangesetView({ summary }: { summary: ChangesetSummary }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <div className="space-y-3">
      {summary.moves.length > 0 && (
        <Group title={t(($) => $.moves)}>
          <MoveList moves={summary.moves} />
        </Group>
      )}
      {summary.creates.length > 0 && (
        <Group title={t(($) => $.creates)}>
          <Diffs files={summary.creates} />
        </Group>
      )}
      {summary.updates.length > 0 && (
        <Group title={t(($) => $.updates)}>
          <Diffs files={summary.updates} />
        </Group>
      )}
      {summary.deletes.length > 0 && (
        <Group title={t(($) => $.deletes)}>
          <PathList paths={summary.deletes} />
        </Group>
      )}
      {summary.mkdirs.length > 0 && (
        <Group title={t(($) => $.mkdirs)}>
          <PathList paths={summary.mkdirs} />
        </Group>
      )}
    </div>
  );
}
