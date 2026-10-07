import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { ToolCard } from "@/components/chat/tools/ToolCard";
import { PathCode, ToolOutputResult, ToolPre, ToolSection } from "@/components/ToolDisplay";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { keyPrefix } from "@/i18n";
import { type ToolPart, toolDisplayName, toolInputAs } from "@/lib/chat/tool-part";
import { basename } from "@/lib/utils";

const T_OPTIONS = keyPrefix(($) => $.chat.tools.mutations);

/** Lines of written content shown before the preview collapses. */
const PREVIEW_LINES = 12;

/** Mirrors `WriteDocumentTool` in `backend/src/hivegent/tools/mutations.py`. */
const WriteInputSchema = z.object({
  file_path: z.string(),
  content: z.string(),
  mode: z.enum(["replace", "create", "append", "prepend"]).default("replace"),
});

function ContentPreview({ content }: { content: string }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const [expanded, setExpanded] = useState(false);
  const lines = useMemo(() => content.replace(/\n$/, "").split("\n"), [content]);
  const collapsed = !expanded && lines.length > PREVIEW_LINES;

  if (!content) return <p className="text-muted-foreground">{t(($) => $.emptyContent)}</p>;

  return (
    <div className="space-y-1">
      <ToolPre boxed>
        {collapsed ? `${lines.slice(0, PREVIEW_LINES).join("\n")}\n…` : content}
      </ToolPre>
      {lines.length > PREVIEW_LINES && (
        <Button variant="link" size="xs" className="px-0" onClick={() => setExpanded(!expanded)}>
          {expanded ? t(($) => $.showLess) : t(($) => $.showAll, { count: lines.length })}
        </Button>
      )}
    </div>
  );
}

/**
 * The content a write sets, collapsed when long, which the approval's diff
 * shows only while the call waits and only up to its cap.
 */
export function WriteDocumentTool({ part }: { part: ToolPart }) {
  const { t } = useTranslation();
  const input = toolInputAs(part, WriteInputSchema);
  const tool = toolDisplayName(t, "write_document");
  const name = input && basename(input.file_path);

  return (
    <ToolCard
      toolName="write_document"
      part={part}
      title={name ? t(($) => $.chat.tools.mutations.titleWithPath, { tool, name }) : tool}
      parameters={
        input && (
          <ToolSection title={t(($) => $.chat.tools.mutations.changes)}>
            <div className="flex items-center gap-2">
              <PathCode>{input.file_path}</PathCode>
              <Badge variant="secondary">
                {t(($) => $.chat.tools.mutations.modes[input.mode])}
              </Badge>
            </div>
            <ContentPreview content={input.content} />
          </ToolSection>
        )
      }
    >
      <ToolOutputResult part={part} />
    </ToolCard>
  );
}
