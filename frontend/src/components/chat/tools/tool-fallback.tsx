import type { ComponentProps } from "react";
import { ToolCard } from "@/components/chat/tools/tool-card";
import { ToolOutputResult } from "@/components/chat/tools/display";

type ToolFallbackProps = Pick<
  ComponentProps<typeof ToolCard>,
  "toolName" | "part" | "prompt" | "parameters"
>;

/** A call of a tool without a view of its own, with its raw arguments and output. */
export function ToolFallback({ toolName, part, prompt, parameters }: ToolFallbackProps) {
  return (
    <ToolCard toolName={toolName} part={part} prompt={prompt} parameters={parameters}>
      <ToolOutputResult part={part} />
    </ToolCard>
  );
}
