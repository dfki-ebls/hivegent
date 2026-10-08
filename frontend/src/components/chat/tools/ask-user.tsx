import { CheckIcon, MessageCircleQuestionIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Question,
  QuestionInput,
  QuestionOption,
  QuestionOptions,
  QuestionPrompt,
  type QuestionValue,
} from "@/components/ai-elements/question";
import { Tool, ToolContent } from "@/components/ai-elements/tool";
import { ToolCardHeader } from "@/components/chat/tools/ToolCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useToolApproval } from "@/hooks/chat/use-tool-approval";
import { keyPrefix } from "@/i18n";
import {
  ASK_USER_TOOL,
  type AskAnswer,
  AskAnswersSchema,
  AskInputSchema,
  type AskQuestion,
  DISMISSED_ERROR,
} from "@/lib/chat/ask-user";
import { type ToolPart, parseJson, toolDisplayName, toolInputAs } from "@/lib/chat/tool-part";

const T_OPTIONS = keyPrefix(($) => $.chat.tools.ask);

/** The value of the "Other" choice, which no label the model writes can equal. */
const OTHER = "\u0000other";

interface Draft {
  value: QuestionValue;
  note: string;
}

const EMPTY_DRAFT: Draft = { value: { selectedValues: [], text: "" }, note: "" };

/** The status label of each state a question card shows its own way. */
const STATUS: Partial<Record<ToolPart["state"], "pending" | "answered" | "dismissed">> = {
  "input-available": "pending",
  "output-available": "answered",
  "output-error": "dismissed",
};

/** The answer a draft gives, or none while it neither picks an option nor types one. */
function toAnswer({ value, note }: Draft): AskAnswer | undefined {
  const selected = value.selectedValues.filter((label) => label !== OTHER);
  const other = value.selectedValues.includes(OTHER) ? value.text.trim() : "";
  const trimmedNote = note.trim();

  if (selected.length === 0 && !other) return undefined;

  return { selected, ...(other && { other }), ...(trimmedNote && { note: trimmedNote }) };
}

interface QuestionFieldsProps {
  question: AskQuestion;
  draft: Draft;
  onChange: (draft: Draft) => void;
  disabled: boolean;
}

function QuestionFields({ question, draft, onChange, disabled }: QuestionFieldsProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <Question
      className="border-0 p-0"
      selectionMode={question.multi_select ? "multiple" : "single"}
      value={draft.value}
      onValueChange={(value) => onChange({ ...draft, value })}
      disabled={disabled}
    >
      <QuestionPrompt>{question.question}</QuestionPrompt>
      <QuestionOptions className="flex-col items-stretch">
        {question.options.map((option) => (
          <QuestionOption key={option.label} value={option.label} className="justify-start py-2">
            <span className="flex flex-col items-start gap-0.5 text-left">
              <span className="flex items-center gap-2">
                {option.label}
                {option.recommended && (
                  <Badge variant="secondary">{t(($) => $.recommended)}</Badge>
                )}
              </span>
              {option.description && (
                <span className="font-normal text-xs opacity-80">{option.description}</span>
              )}
            </span>
          </QuestionOption>
        ))}
        <QuestionOption value={OTHER} className="justify-start py-2">
          {t(($) => $.other)}
        </QuestionOption>
      </QuestionOptions>
      {draft.value.selectedValues.includes(OTHER) && (
        <QuestionInput
          aria-label={t(($) => $.other)}
          placeholder={t(($) => $.otherPlaceholder)}
        />
      )}
      <Textarea
        className="min-h-12"
        aria-label={t(($) => $.note)}
        placeholder={t(($) => $.notePlaceholder)}
        value={draft.note}
        onChange={(event) => onChange({ ...draft, note: event.target.value })}
        disabled={disabled}
      />
    </Question>
  );
}

/** The questions as one form, a tab each when there are several, answered all at once. */
function AskForm({ toolCallId, questions }: { toolCallId: string; questions: AskQuestion[] }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const { addToolOutput, blockedReason } = useToolApproval();
  const [drafts, setDrafts] = useState<Draft[]>(() => questions.map(() => EMPTY_DRAFT));
  const call = { tool: ASK_USER_TOOL, toolCallId };
  const answers = drafts.map(toAnswer);
  const complete = answers.filter((item): item is AskAnswer => item !== undefined);
  const blocked = blockedReason !== undefined;

  const fields = (question: AskQuestion, index: number) => (
    <QuestionFields
      question={question}
      draft={drafts[index] ?? EMPTY_DRAFT}
      onChange={(draft) => setDrafts((prev) => prev.with(index, draft))}
      disabled={blocked}
    />
  );

  return (
    <div className="space-y-4">
      {questions.length === 1 ? (
        fields(questions[0], 0)
      ) : (
        <Tabs defaultValue="0">
          <TabsList className="max-w-full flex-wrap">
            {questions.map((question, index) => (
              <TabsTrigger key={index} value={String(index)}>
                {answers[index] && <CheckIcon className="size-3.5" />}
                {question.header}
              </TabsTrigger>
            ))}
          </TabsList>
          {questions.map((question, index) => (
            <TabsContent key={index} value={String(index)}>
              {fields(question, index)}
            </TabsContent>
          ))}
        </Tabs>
      )}
      <div className="flex items-center justify-end gap-2">
        <span className="mr-auto text-xs text-muted-foreground">
          {blockedReason ??
            (questions.length > 1 &&
              t(($) => $.progress, { answered: complete.length, count: questions.length }))}
        </span>
        <Button variant="ghost" size="sm" disabled={blocked} onClick={() =>
            void addToolOutput({ ...call, state: "output-error", errorText: DISMISSED_ERROR })
          }>
          {t(($) => $.dismiss)}
        </Button>
        <Button
          size="sm"
          disabled={blocked || complete.length < questions.length}
          onClick={() => void addToolOutput({ ...call, output: complete })}
        >
          {t(($) => $.submit)}
        </Button>
      </div>
    </div>
  );
}

/** Each question with what the user answered, once they have. */
function AskSummary({ questions, answers }: { questions: AskQuestion[]; answers: AskAnswer[] }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <dl className="space-y-3 text-sm">
      {questions.map((question, index) => {
        const reply = answers[index];

        return (
          <div key={index} className="space-y-0.5">
            <dt className="text-muted-foreground">{question.question}</dt>
            {reply && (
              <dd className="font-medium">
                {[...reply.selected, ...(reply.other ? [reply.other] : [])].join(", ")}
              </dd>
            )}
            {reply?.note && (
              <dd className="whitespace-pre-wrap">
                <span className="text-muted-foreground">{t(($) => $.yourNote)} </span>
                {reply.note}
              </dd>
            )}
          </div>
        );
      })}
    </dl>
  );
}

/**
 * The questions the assistant asks with `ask_user`, answered right in the card.
 * The answers are the call's output, so the run continues with them once sent.
 */
export function AskUserTool({ part }: { part: ToolPart }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const questions = toolInputAs(part, AskInputSchema)?.questions;
  const answers =
    part.state === "output-available"
      ? AskAnswersSchema.safeParse(parseJson(part.output)).data
      : undefined;
  const pending = part.state === "input-available";
  const status = STATUS[part.state];

  return (
    <Tool open={open || pending} onOpenChange={setOpen} className="mb-0">
      <ToolCardHeader
        title={toolDisplayName(t, ASK_USER_TOOL)}
        state={part.state}
        status={status && t(($) => $.chat.tools.ask.status[status])}
        icon={<MessageCircleQuestionIcon className="size-4 text-muted-foreground" />}
      />
      <ToolContent>
        {questions && pending && part.toolCallId && (
          <AskForm toolCallId={part.toolCallId} questions={questions} />
        )}
        {questions && answers && <AskSummary questions={questions} answers={answers} />}
        {part.state === "output-error" && (
          <p className="text-sm text-muted-foreground">{t(($) => $.chat.tools.ask.dismissed)}</p>
        )}
      </ToolContent>
    </Tool>
  );
}
