"""Compaction of a run's history with exar, the summary asked for under the run's own prefix.

`exar.pydantic_ai.compaction` compacts a history the context window refused
and sends later requests only the window from the summary on (see
"Compaction is a turn of the conversation" in ``backend/README.md``).  The
summary is one more turn of the conversation, under the prefix its turns ran
under (:class:`~.common.RunPrefix`), so the provider answers everything ahead
of the prompt from its cache.  The prompt is asked in the run's language.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from exar.agents import call
from exar.pydantic_ai import PydanticAiAgent
from exar.pydantic_ai.compaction import (
    COMPACTION,
    CompactOnOverflow,
    is_context_overflow,
    is_server_overflow,
    run_summary,
    summarize,
)
from pydantic_ai import AgentRunResult, RunContext
from pydantic_ai.messages import CompactionPart, ModelMessage
from pydantic_ai.usage import RunUsage, UsageLimits

from ..llm import SUMMARY_MAX_TOKENS, summary_model_settings
from ..shared.l10n import Localized
from .app import summary_agent
from .common import RunPrefix, UserDeps

__all__ = [
    "COMPACT_PROMPT",
    "PrefixSummary",
    "compact_on_overflow",
    "is_overflow",
]

# Section structure adapted from pi-mono (MIT), https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/src/core/compaction/compaction.ts
COMPACT_PROMPT = Localized(
    en="""\
Summarize the conversation as a handover note for continuing the work in a fresh context.

Use this exact structure, keeping every heading even where a section is empty:

## Goal
[What the user is trying to accomplish, in one or two sentences.]

## Constraints and decisions
- [Preferences, requirements, and choices made, each with its reason, otherwise "(none)"]

## Progress
### Done
- [Finished work and established findings, otherwise "(none)"]
### In progress
- [Work underway or partially applied, otherwise "(none)"]
### Blocked
- [Blockers, failures, and unknowns, otherwise "(none)"]

## Documents
- [Full workspace path: why it matters, otherwise "(none)"]

## Next steps
1. [The immediate concrete action, otherwise "(none)"]

Keep every section to terse bullets rather than prose, and reproduce workspace paths, quoted passages, names, and figures exactly as they appeared.
Answer from the conversation above alone, and do not call any tools.
Return only the summary, with no commentary around it.""",
    de="""\
Fasse die Konversation als Übergabenotiz zusammen, damit die Arbeit in einem frischen Kontext weitergehen kann.

Verwende genau diese Struktur und behalte jede Überschrift bei, auch wenn ein Abschnitt leer ist:

## Ziel
[Was die Benutzer:in erreichen möchte, in ein oder zwei Sätzen.]

## Vorgaben und Entscheidungen
- [Vorlieben, Anforderungen und getroffene Entscheidungen, jeweils mit Begründung, sonst „(keine)“]

## Fortschritt
### Erledigt
- [Abgeschlossene Arbeit und gesicherte Erkenntnisse, sonst „(keine)“]
### In Arbeit
- [Laufende oder teilweise umgesetzte Arbeit, sonst „(keine)“]
### Blockiert
- [Hindernisse, Fehlschläge und offene Fragen, sonst „(keine)“]

## Dokumente
- [Vollständiger Pfad im Arbeitsbereich: warum es wichtig ist, sonst „(keine)“]

## Nächste Schritte
1. [Die unmittelbar nächste konkrete Aktion, sonst „(keine)“]

Halte jeden Abschnitt in knappen Aufzählungspunkten statt in Fließtext, und übernimm Pfade im Arbeitsbereich, zitierte Passagen, Namen und Zahlen genau so, wie sie vorkamen.
Antworte allein aus der Konversation oben und ruf keine Tools auf.
Gib nur die Zusammenfassung zurück, ohne Kommentar drumherum.""",
)
"""The request, appended to the live conversation as its last user turn.

Written in the run's language: an English user turn at the end of a German
conversation would pull the summary, and every turn continuing from it, into
English.

Bounded by structure rather than by a word count, which would make the model
drop sections instead of tightening them.  The tools stay declared, so the
closing sentences are what holds the model back from calling one:
withdrawing them, or sending ``tool_choice='none'``, would rewrite the block
that sits ahead of the messages and throw away the prefix this path exists for.
A summary that calls one anyway is asked again with the tools withheld.
"""

# The tools stay declared so the prefix stays identical, every call is refused before it runs.
_LIMITS = UsageLimits(request_limit=1, tool_calls_limit=0)


def is_overflow(error: Exception, messages: Sequence[ModelMessage]) -> bool:
    """Whether *error*, with the *messages* its run ended on, means a request did not fit the context window, self-hosted endpoints included."""
    return is_context_overflow(error, messages) or is_server_overflow(error)


@dataclass(frozen=True, slots=True, kw_only=True)
class PrefixSummary:
    """Asks for the summary of a history as its next turn under *prefix*, an `exar` summarizer.

    Args:
        prefix: The prefix the history ran under, which the request reproduces.
    """

    prefix: RunPrefix

    @property
    def max_tokens(self) -> int:
        """The output cap of the summary, which a refused summary sheds."""
        return summary_model_settings(self.prefix.llm).get(
            "max_tokens", SUMMARY_MAX_TOKENS
        )

    async def ask(self, messages: Sequence[ModelMessage], usage: RunUsage, /) -> str:
        """The summary of *messages*, which must fit the window, its usage added to *usage*.

        The summary runs on a usage of its own, since a shared one would count
        the turn's requests against its ``request_limit`` of one.
        """
        run = self.prefix

        async def summarize_once(prompt: str) -> AgentRunResult[str]:
            result = await run_summary(
                lambda withheld: summary_agent.iter(
                    prompt,
                    message_history=list(messages),
                    **run.run_kwargs(),
                    model_settings=summary_model_settings(run.llm),
                    capabilities=[*run.capabilities, *withheld],
                    usage_limits=_LIMITS,
                    metadata={COMPACTION: True},
                )
            )
            usage.incr(result.usage)

            return result

        summary = PydanticAiAgent[str, str](run=summarize_once, name="summarize")

        return (await call(summary, COMPACT_PROMPT[run.deps.language])).strip()

    async def __call__(
        self, ctx: RunContext[UserDeps], messages: Sequence[ModelMessage], /
    ) -> str:
        """The summary of *messages*, counted into the usage of the run it compacts."""
        return await self.ask(messages, ctx.usage)

    async def compact(
        self, messages: Sequence[ModelMessage], usage: RunUsage, /
    ) -> CompactionPart:
        """The compaction of *messages*, shedding from their tail while the summary overflows.

        The usage of the summary is added to *usage*.
        """

        async def ask(prefix: Sequence[ModelMessage]) -> str:
            return await self.ask(prefix, usage)

        summary = await summarize(
            messages, ask, max_tokens=self.max_tokens, is_overflow=is_overflow
        )

        return CompactionPart(content=summary)


def compact_on_overflow(prefix: RunPrefix) -> CompactOnOverflow[UserDeps]:
    """The capability that compacts a run of *prefix* the context window refused."""
    summary = PrefixSummary(prefix=prefix)

    return CompactOnOverflow(
        summarizer=summary, max_tokens=summary.max_tokens, is_overflow=is_overflow
    )
