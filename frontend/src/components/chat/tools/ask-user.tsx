import { MessageCircleQuestionIcon } from "lucide-react";
import type { SubmitEvent } from "react";
import { useTranslation } from "react-i18next";
import { TraceStepCollapsible } from "@/components/chat/trace-step";
import { PromptItem } from "@/components/chat/tools/approval";
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
import { useChatContext, useMessageLive } from "@/components/chat/chat-context";
import { keyPrefix } from "@/i18n";
import {
  ASK_USER_TOOL,
  type AskAnswer,
  AskAnswersSchema,
  AskInputSchema,
  type AskQuestion,
  DISMISSED_ERROR,
} from "@/lib/chat/ask-user";
import {
  type ToolRenderProps,
  parseJson,
  toolDisplayName,
  toolInputAs,
  toolLineState,
} from "@/lib/chat/tool-part";

const T_OPTIONS = keyPrefix(($) => $.chat.tools.ask);

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
  const { chat } = useChatContext();
  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    void chat.addToolOutput({
      tool: ASK_USER_TOOL,
      toolCallId,
      output: readAnswers(event.currentTarget, questions),
    });
  };

  return (
    <Questionnaire onSubmit={submit} className="basis-full gap-4">
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
          className="gap-3"
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
        <QuestionnaireSubmit size="sm">{t(($) => $.submit)}</QuestionnaireSubmit>
      </QuestionnaireActions>
    </Questionnaire>
  );
}

/**
 * Questions that wait for the user's answers, shown once the run waits for them.
 * They ask rather than report, so they stand outside the trace until they are answered.
 */
function AskPrompt({ toolCallId, questions }: { toolCallId: string; questions: AskQuestion[] }) {
  const { t } = useTranslation();
  const { chat } = useChatContext();
  const title = toolDisplayName(t, ASK_USER_TOOL);

  return (
    <PromptItem
      icon={<MessageCircleQuestionIcon />}
      title={title}
      actions={
        <Button
          variant="ghost"
          size="sm"
          onClick={() =>
            void chat.addToolOutput({
              tool: ASK_USER_TOOL,
              toolCallId,
              state: "output-error",
              errorText: DISMISSED_ERROR,
            })
          }
        >
          {t(($) => $.chat.tools.ask.dismiss)}
        </Button>
      }
    >
      <AskForm toolCallId={toolCallId} questions={questions} />
    </PromptItem>
  );
}

/** Each question with what the user answered, once they have. */
function AskSummary({ questions, answers }: { questions: AskQuestion[]; answers: AskAnswer[] }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <dl className="flex flex-col gap-3">
      {questions.map((question, index) => {
        const reply = answers[index];

        return (
          <div key={index} className="flex flex-col gap-0.5">
            <dt className="text-muted-foreground">{question.question}</dt>
            {reply && (
              <dd className="font-medium text-foreground">
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
 * The questions the assistant asks with `ask_user`, answered right in the prompt.
 * The answers are the call's output, so the run continues with them once sent.
 */
export function AskUserTool({ part, prompt }: ToolRenderProps) {
  const { t } = useTranslation();
  const live = useMessageLive();
  const questions = toolInputAs(part, AskInputSchema)?.questions;
  const title = toolDisplayName(t, ASK_USER_TOOL);

  if (prompt && questions && part.toolCallId) {
    return <AskPrompt toolCallId={part.toolCallId} questions={questions} />;
  }

  const answers =
    part.state === "output-available"
      ? AskAnswersSchema.safeParse(parseJson(part.output)).data
      : undefined;

  const { label } = toolLineState(part, live);
  const description =
    part.state === "output-error"
      ? t(($) => $.chat.tools.ask.dismissed)
      : answers
        ? t(($) => $.chat.tools.ask.answered)
        : label && t(($) => $.chat.tools.status[label]);

  return (
    <TraceStepCollapsible
      icon={<MessageCircleQuestionIcon />}
      title={title}
      status={label === "running" ? "running" : undefined}
      description={description}
    >
      {questions && answers && <AskSummary questions={questions} answers={answers} />}
    </TraceStepCollapsible>
  );
}
