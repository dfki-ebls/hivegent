import { MessageCircleQuestionIcon } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { TraceStepTrigger } from "@/components/chat/TraceStep";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Questionnaire,
  QuestionnaireActions,
  QuestionnaireChoice,
  QuestionnaireChoiceDescription,
  QuestionnaireChoices,
  QuestionnaireError,
  QuestionnaireInput,
  QuestionnaireItem,
  QuestionnaireNext,
  QuestionnairePrevious,
  QuestionnaireProgress,
  QuestionnaireSubmit,
  QuestionnaireTitle,
} from "@/components/ui/questionnaire";
import { Textarea } from "@/components/ui/textarea";
import { TraceCollapsible, TraceCollapsibleContent } from "@/components/ui/trace";
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

/** The status label of each state a question card shows its own way. */
const STATUS: Partial<Record<ToolPart["state"], "pending" | "answered" | "dismissed">> = {
  "input-available": "pending",
  "output-available": "answered",
  "output-error": "dismissed",
};

/** The form field of the question at *index*, and of its note. */
const answerField = (index: number) => `question-${index}`;
const noteField = (index: number) => `${answerField(index)}-note`;

/**
 * The answers the submitted form gives, one per question.
 * The checked options are the picks, and the text field carries the typed answer.
 */
function readAnswers(form: HTMLFormElement, questions: AskQuestion[]): AskAnswer[] {
  const fields = Array.from(form.elements);

  return questions.map((_, index) => {
    const inputs = fields.filter(
      (field): field is HTMLInputElement =>
        field instanceof HTMLInputElement && field.name === answerField(index),
    );
    const choices = inputs.filter((input) => input.type === "radio" || input.type === "checkbox");
    const other = inputs.find((input) => !choices.includes(input))?.value.trim();
    const note = fields
      .find(
        (field): field is HTMLTextAreaElement =>
          field instanceof HTMLTextAreaElement && field.name === noteField(index),
      )
      ?.value.trim();

    return {
      selected: choices.filter((input) => input.checked).map((input) => input.value),
      ...(other && { other }),
      ...(note && { note }),
    };
  });
}

/** The questions as one questionnaire, a step each, answered all at once. */
function AskForm({ toolCallId, questions }: { toolCallId: string; questions: AskQuestion[] }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const { addToolOutput, blockedReason } = useToolApproval();
  const call = { tool: ASK_USER_TOOL, toolCallId };
  const blocked = blockedReason !== undefined;
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void addToolOutput({ ...call, output: readAnswers(event.currentTarget, questions) });
  };

  return (
    <Questionnaire onSubmit={submit} className="gap-4">
      <QuestionnaireProgress
        render={(props, { current, total }) => (
          <div {...props}>
            {questions[current - 1]?.header}
            {total > 1 && ` · ${t(($) => $.progress, { current, count: total })}`}
          </div>
        )}
      />
      {questions.map((question, index) => (
        <QuestionnaireItem
          key={index}
          name={answerField(index)}
          required
          multiple={question.multi_select}
        >
          <QuestionnaireTitle>{question.question}</QuestionnaireTitle>
          <QuestionnaireChoices>
            {question.options.map((option) => (
              <QuestionnaireChoice key={option.label} value={option.label}>
                <span className="flex items-center gap-2 font-medium">
                  {option.label}
                  {option.recommended && (
                    <Badge variant="secondary">{t(($) => $.recommended)}</Badge>
                  )}
                </span>
                {option.description && (
                  <QuestionnaireChoiceDescription>
                    {option.description}
                  </QuestionnaireChoiceDescription>
                )}
              </QuestionnaireChoice>
            ))}
            <QuestionnaireInput
              aria-label={t(($) => $.other)}
              placeholder={t(($) => $.otherPlaceholder)}
            />
          </QuestionnaireChoices>
          <Textarea
            name={noteField(index)}
            className="min-h-12"
            aria-label={t(($) => $.note)}
            placeholder={t(($) => $.notePlaceholder)}
          />
          <QuestionnaireError>{t(($) => $.unanswered)}</QuestionnaireError>
        </QuestionnaireItem>
      ))}
      <QuestionnaireActions>
        <QuestionnairePrevious size="sm">{t(($) => $.previous)}</QuestionnairePrevious>
        <QuestionnaireNext size="sm">{t(($) => $.next)}</QuestionnaireNext>
        <QuestionnaireSubmit size="sm" disabled={blocked}>
          {t(($) => $.submit)}
        </QuestionnaireSubmit>
      </QuestionnaireActions>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">{blockedReason}</span>
        <Button
          variant="ghost"
          size="sm"
          disabled={blocked}
          onClick={() =>
            void addToolOutput({ ...call, state: "output-error", errorText: DISMISSED_ERROR })
          }
        >
          {t(($) => $.dismiss)}
        </Button>
      </div>
    </Questionnaire>
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
    <TraceCollapsible open={open || pending} onOpenChange={setOpen}>
      <TraceStepTrigger
        icon={<MessageCircleQuestionIcon />}
        title={toolDisplayName(t, ASK_USER_TOOL)}
        status={part.state === "input-streaming" ? "pending" : undefined}
        description={status && t(($) => $.chat.tools.ask.status[status])}
      />
      <TraceCollapsibleContent className="text-foreground">
        {questions && pending && part.toolCallId && (
          <AskForm toolCallId={part.toolCallId} questions={questions} />
        )}
        {questions && answers && <AskSummary questions={questions} answers={answers} />}
        {part.state === "output-error" && (
          <p className="text-sm text-muted-foreground">{t(($) => $.chat.tools.ask.dismissed)}</p>
        )}
      </TraceCollapsibleContent>
    </TraceCollapsible>
  );
}
