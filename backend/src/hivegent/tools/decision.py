"""Questions to the decision model, offered to a sandboxed program alone.

The decision counterparts of ``complete``: `judge` answers a yes/no question with
the probability of yes and `decide` picks one of named options, each awaited inside
the program, so filtering, ranking, or labeling many items is one loop.  The
decisions themselves are injected, which keeps this module free of any model,
settings, or run.
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated

from exar.tools import Retry, effect
from exar.tools.batch import tool_return
from pydantic import Field
from pydantic_ai import ToolReturn

from ..shared.l10n import Localized
from .base import ToolFunc

__all__ = [
    "DECIDE_INSTRUCTIONS",
    "JUDGE_INSTRUCTIONS",
    "Choice",
    "Judgement",
    "decide",
    "judge",
]

JUDGE_INSTRUCTIONS: Localized[str] = Localized(
    en="""
Use `judge` to answer a yes/no question about each of many items in a loop or with `asyncio.gather`, such as to filter or rank them. It returns the probability of yes from 0 to 1, faster and cheaper than `complete`, and every call counts against the same budget per turn.
""",
    de="""
Nutze `judge`, um eine Ja/Nein-Frage zu jedem von vielen Elementen in einer Schleife oder mit `asyncio.gather` zu beantworten, etwa um sie zu filtern oder zu ordnen. Die Funktion gibt die Wahrscheinlichkeit für Ja von 0 bis 1 zurück, schneller und günstiger als `complete`, und jeder Aufruf zählt gegen dasselbe Budget pro Runde.
""",
)
"""Added only where ``judge`` is live, since the prose names it."""

DECIDE_INSTRUCTIONS: Localized[str] = Localized(
    en="""
Use `decide` to label each of many items with one of fixed `options` in a loop or with `asyncio.gather`, such as a category or a priority. It returns the option picked, faster and cheaper than `complete`, and every call counts against the same budget per turn.
""",
    de="""
Nutze `decide`, um jedes von vielen Elementen in einer Schleife oder mit `asyncio.gather` mit einer von festen `options` zu kennzeichnen, etwa einer Kategorie oder Priorität. Die Funktion gibt die gewählte Option zurück, schneller und günstiger als `complete`, und jeder Aufruf zählt gegen dasselbe Budget pro Runde.
""",
)
"""Added only where ``decide`` is live, since the prose names it."""

type Judgement = Callable[[str, str], Awaitable[float]]
"""Answer a yes/no question about a text with the probability of yes."""

type Choice = Callable[[str, str, Sequence[str]], Awaitable[str]]
"""Pick the option that answers a question about a text."""

QuestionArg = Annotated[
    str,
    Field(description="The question, such as 'Does the text report a defect?'."),
]

TextArg = Annotated[
    str, Field(description="The text the question is about, all the model sees.")
]

OptionsArg = Annotated[
    list[str],
    Field(
        description="The distinct options to pick from, at least two, such as ['billing', 'bug', 'other'].",
        min_length=2,
    ),
]


def judge(judgement: Judgement) -> ToolFunc:
    """The tool that answers yes/no questions about texts through *judgement*."""

    @effect("read")
    async def judge(question: QuestionArg, text: TextArg) -> ToolReturn[float]:
        """Answer the yes/no `question` about `text` with the probability of yes, from 0 to 1.

        The model has no tools, no documents, and no memory of earlier calls.
        Every call counts against a fixed budget per turn.
        """
        return tool_return(await judgement(question, text))

    return judge


def decide(choice: Choice) -> ToolFunc:
    """The tool that picks one of the options answering a question about a text through *choice*."""

    @effect("read")
    async def decide(
        question: QuestionArg, text: TextArg, options: OptionsArg
    ) -> ToolReturn[str]:
        """Pick the one of `options` that answers `question` about `text` best, and return it.

        The model has no tools, no documents, and no memory of earlier calls.
        Every call counts against a fixed budget per turn.
        """
        if len(set(options)) != len(options):
            raise Retry("options must be distinct, pass each option once.")

        return tool_return(await choice(question, text, options))

    return decide
