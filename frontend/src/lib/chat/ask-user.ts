import { z } from "zod";

/** The tool the assistant asks the user with, see `backend/src/hivegent/agents/tools/ask.py`. */
export const ASK_USER_TOOL = "ask_user";

export const ASK_USER_PART = `tool-${ASK_USER_TOOL}`;

/** The output of a dismissed call, whose wording the backend replaces with its own. */
export const DISMISSED_ERROR = "The user dismissed the questions.";

/** Mirrors `AskQuestion` in the backend. */
const AskQuestionSchema = z.object({
  header: z.string(),
  question: z.string(),
  options: z.array(
    z.object({
      label: z.string(),
      description: z.string().nullish(),
      recommended: z.boolean().default(false),
    }),
  ),
  multi_select: z.boolean().default(false),
});

export const AskInputSchema = z.object({ questions: z.array(AskQuestionSchema).min(1) });

/** Mirrors `AskAnswer` in the backend, one per question in the order asked. */
const AskAnswerSchema = z.object({
  selected: z.array(z.string()),
  other: z.string().optional(),
  note: z.string().optional(),
});

export const AskAnswersSchema = z.array(AskAnswerSchema);

export type AskQuestion = z.infer<typeof AskQuestionSchema>;
export type AskAnswer = z.infer<typeof AskAnswerSchema>;
