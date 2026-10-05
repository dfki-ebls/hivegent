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
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { keyPrefix } from "@/i18n";
import { type ToolPart, toolInput } from "@/lib/chat/tool-part";

const T_OPTIONS = keyPrefix(($) => $.chat.tools.plan);

interface CreatePlanToolProps {
  part: ToolPart;
  onExecutePlan?: () => void;
}

export function CreatePlanTool({ part, onExecutePlan }: CreatePlanToolProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const state: ToolPart["state"] = part.state ?? "output-available";
  const input = toolInput<{ title?: string; description?: string; steps?: string[] }>(part);

  return (
    <Plan defaultOpen isStreaming={state === "input-streaming"}>
      <PlanHeader>
        <div>
          <PlanTitle>{input?.title ?? t(($) => $.fallbackTitle)}</PlanTitle>
          {input?.description && <PlanDescription>{input.description}</PlanDescription>}
        </div>
        <PlanAction>
          <PlanTrigger aria-label={t(($) => $.toggle)} />
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
          <Button onClick={onExecutePlan}>{t(($) => $.execute)}</Button>
        </PlanFooter>
      )}
    </Plan>
  );
}
