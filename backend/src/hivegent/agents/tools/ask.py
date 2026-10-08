"""Structured questions the agent puts to the user mid-run.

``ask_user`` has no body of its own: it defers the call, so the run ends with
the questions pending and the client answers them as the call's output.  The
chat adapter validates that output against :data:`ANSWERS` and resumes the run
with it as the call's result (``server/vercel.py``).
"""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from pydantic_ai import FunctionToolset
from pydantic_ai.exceptions import CallDeferred

from ..common import UserDeps

__all__ = [
    "ANSWERS",
    "ASK_USER_TOOL",
    "DISMISSED_QUESTIONS",
    "AskAnswer",
    "AskOption",
    "AskQuestion",
    "ask_toolset",
    "ask_user",
]

ASK_USER_TOOL = "ask_user"

DISMISSED_QUESTIONS = (
    "The user dismissed these questions without answering. Do not ask them "
    "again unless the user brings the topic up. Continue with what you know, or "
    "with what the user wrote next."
)
"""What the model reads for questions the user closed or walked away from."""


class AskOption(BaseModel):
    """One answer the user can pick."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(
        min_length=1, max_length=60, description="The choice itself, 1-5 words."
    )
    description: str | None = Field(
        default=None,
        max_length=200,
        description="One sentence on what picking this option implies.",
    )
    recommended: bool = Field(
        default=False, description="Mark the option you would pick yourself."
    )


class AskQuestion(BaseModel):
    """One question with the options the user picks from."""

    model_config = ConfigDict(extra="forbid")

    header: str = Field(
        min_length=1,
        max_length=20,
        description="Very short tab label naming the topic, e.g. 'Scope'.",
    )
    question: str = Field(
        min_length=1,
        max_length=500,
        description="The complete question, ending with a question mark.",
    )
    options: list[AskOption] = Field(
        min_length=2,
        max_length=4,
        description=(
            "Distinct choices. Never add an 'Other' option, the user can always "
            "type their own answer and add a note."
        ),
    )
    multi_select: bool = Field(
        default=False,
        description="Let the user pick several options instead of exactly one.",
    )

    @model_validator(mode="after")
    def _check_options(self) -> Self:
        labels = [option.label for option in self.options]

        if len(set(labels)) != len(labels):
            raise ValueError("option labels must be unique")

        if not self.multi_select and sum(o.recommended for o in self.options) > 1:
            raise ValueError("a single-select question recommends at most one option")

        return self


class AskAnswer(BaseModel):
    """The user's answer to one question, in the order the questions were asked.

    >>> AskAnswer(selected=[], other=" Both ").other
    'Both'
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    selected: list[str] = Field(default_factory=list[str])
    other: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _check_answered(self) -> Self:
        if not self.selected and not self.other:
            raise ValueError("an answer selects an option or gives its own")

        return self


ANSWERS = TypeAdapter(list[AskAnswer])
"""Validator for the client's output of an ``ask_user`` call."""

QuestionsArg = Annotated[
    list[AskQuestion],
    Field(min_length=1, max_length=4, description="The questions, asked together."),
]

ask_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@ask_toolset.tool_plain(name=ASK_USER_TOOL)
def ask_user(questions: QuestionsArg) -> list[AskAnswer]:
    """Ask the user multiple-choice questions and wait for their answers.

    Use it when a decision is the user's to make and you cannot settle it from
    the request, the documents, or a sensible default, e.g. to resolve an
    ambiguous request or to choose between approaches.  Do not use it for
    yes/no confirmations of an action, and do not ask what a tool could find
    out.  Put the recommended option first and mark it.

    The result is one answer per question, in order: ``selected`` holds the
    labels the user picked, ``other`` an answer they typed instead, and
    ``note`` extra context.  Follow what they actually say, notes included.
    """
    raise CallDeferred
