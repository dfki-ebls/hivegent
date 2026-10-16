import { useTranslation } from "react-i18next";
import { z } from "zod";
import { ToolCard } from "@/components/chat/tools/ToolCard";
import { ToolCode, ToolOutputResult } from "@/components/chat/tools/display";
import { Badge } from "@/components/ui/badge";
import { type ToolPart, toolDisplayName, toolInputAs } from "@/lib/chat/tool-part";
import { basename } from "@/lib/utils";

/** Mirrors `WriteDocumentTool` in `backend/src/hivegent/tools/mutations.py`. */
const WriteInputSchema = z.object({
  file_path: z.string(),
  content: z.string(),
  mode: z.enum(["replace", "create", "append", "prepend"]).default("replace"),
});

/** The content a write sets, highlighted by the file's extension, which the approval's diff only shows up to its cap. */
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
          <ToolCode
            title={input.file_path}
            code={input.content || t(($) => $.chat.tools.mutations.emptyContent)}
            language={input.content ? (name?.split(".").pop() ?? "text") : "text"}
            actions={
              <Badge variant="secondary">
                {t(($) => $.chat.tools.mutations.modes[input.mode])}
              </Badge>
            }
          />
        )
      }
    >
      <ToolOutputResult part={part} />
    </ToolCard>
  );
}
