import { ReasoningContent } from "@/components/ai-elements/reasoning";
import { normalizeDisplayMath } from "@/lib/normalize-math";

interface ReasoningMarkdownProps {
  children: string;
}

export function ReasoningMarkdown({ children }: ReasoningMarkdownProps) {
  return <ReasoningContent>{normalizeDisplayMath(children)}</ReasoningContent>;
}
