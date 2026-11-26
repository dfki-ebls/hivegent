import { BotIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { MarkdownText } from "@/components/chat/markdown/markdown-text";
import { ToolCard } from "@/components/chat/tools/tool-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { type ToolRenderProps, toolInput } from "@/lib/chat/tool-part";
import { useDocumentCanvasStore } from "@/stores/document-canvas-store";

/**
 * The task handed to the subagent, under badges for the other arguments it was given,
 * those of the `delegate_task` tool of pydantic-ai-harness, such as the agent's name.
 */
function SubagentTask({ input: { task, ...args } }: { input: Record<string, unknown> }) {
  const values = Object.values(args).filter((value) => typeof value === "string");

  return (
    <>
      {values.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {values.map((value) => (
            <Badge key={value} variant="secondary" className="font-mono">
              {value}
            </Badge>
          ))}
        </div>
      )}
      {typeof task === "string" && task && <p>{task}</p>}
    </>
  );
}

/**
 * A delegation as a line that stays closed until the user opens it to the task it handed over
 * and its result: the report of a task the agent waited for, or the receipt of one in the
 * background, which runs past the turn. Either run shows in the Tasks tab of the canvas.
 */
export function DelegateTool({ part, prompt }: ToolRenderProps) {
  const { t } = useTranslation();
  const setActiveTab = useDocumentCanvasStore((state) => state.setActiveTab);
  const input = toolInput(part);

  return (
    <ToolCard
      toolName="delegate_task"
      part={part}
      prompt={prompt}
      parameters={input && <SubagentTask input={input} />}
    >
      {typeof part.output === "string" && <MarkdownText>{part.output}</MarkdownText>}
      <Button
        variant="outline"
        size="sm"
        className="self-start"
        onClick={() => setActiveTab("tasks")}
      >
        <BotIcon data-icon="inline-start" />
        {t(($) => $.chat.tools.subagent.showTasks)}
      </Button>
    </ToolCard>
  );
}
