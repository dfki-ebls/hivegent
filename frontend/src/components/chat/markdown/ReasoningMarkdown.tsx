import { ReasoningContent } from "@/components/ai-elements/reasoning";
import { normalizeDisplayMathDelimiters } from "@/lib/normalize-math";

interface ReasoningMarkdownProps {
  children: string;
}

export function ReasoningMarkdown({ children }: ReasoningMarkdownProps) {
  return <ReasoningContent>{normalizeDisplayMathDelimiters(children)}</ReasoningContent>;
}
