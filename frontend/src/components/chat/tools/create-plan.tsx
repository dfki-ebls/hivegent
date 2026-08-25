import {
  Plan,
  PlanAction,
  PlanContent,
  PlanDescription,
  PlanFooter,
  PlanHeader,
  PlanTitle,
  PlanTrigger,
} from "@/components/ai-elements/plan";
import { Button } from "@/components/ui/button";
import { parseJson, type ToolPart } from "@/lib/chat/tool-part";

interface CreatePlanToolProps {
  part: ToolPart;
  onExecutePlan?: () => void;
}

export function CreatePlanTool({ part, onExecutePlan }: CreatePlanToolProps) {
  const state: ToolPart["state"] = part.state ?? "output-available";
  const input = parseJson<{ title?: string; description?: string; steps?: string[] }>(part.input);

  return (
    <Plan defaultOpen isStreaming={state === "input-streaming"}>
      <PlanHeader>
        <div>
          <PlanTitle>{input?.title ?? "Plan"}</PlanTitle>
          {input?.description && <PlanDescription>{input.description}</PlanDescription>}
        </div>
        <PlanAction>
          <PlanTrigger />
        </PlanAction>
      </PlanHeader>
      <PlanContent>
        <ol className="list-decimal space-y-1 pl-5 text-sm">
          {input?.steps?.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      </PlanContent>
      {state === "output-available" && onExecutePlan && (
        <PlanFooter>
          <Button onClick={onExecutePlan}>Execute Plan</Button>
        </PlanFooter>
      )}
    </Plan>
  );
}
