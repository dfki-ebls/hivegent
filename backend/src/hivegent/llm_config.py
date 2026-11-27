"""The models of the operator's catalog, resolved for the calls that use them.

Kept out of :mod:`hivegent.types` so :mod:`hivegent.llm` and the converters can
describe a model without importing the API models, which reach back into the
converter registry.  That import cycle is why this module exists.

Every model comes from ``settings.llm`` alone, so no request can point a call
at another endpoint or spend the operator's key there.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from pydantic_ai.settings import ThinkingEffort

from .config import settings
from .config.llm import (
    DEFAULT_REASONING_EFFORT,
    OPENAI_BASE_URL,
    REASONING_EFFORTS,
    LlmModelSettings,
    LlmProviderSettings,
    ModelFamily,
    ReasoningEffort,
)

__all__ = [
    "LlmConfig",
    "ReasoningEffort",
    "aux_llm",
    "background_llm",
    "chat_llm",
    "clamp_reasoning",
    "decision_llm",
    "model_max_images",
    "run_max_images",
    "stt_llm",
    "turn_llm",
]


@dataclass(frozen=True, slots=True)
class LlmConfig:
    """A catalog model joined with its provider, all a call to it needs.

    ``thinking`` is the effort the call reasons with, ``False`` for none.
    A turn's is its request's, and the ``aux`` model's is always off.
    """

    model: str
    provider: LlmProviderSettings = field(
        default_factory=lambda: LlmProviderSettings(base_url=OPENAI_BASE_URL)
    )
    family: ModelFamily | None = None
    context_window: int | None = None
    max_tokens: int | None = None
    reasoning: tuple[ReasoningEffort, ...] | None = None
    reasoning_overrides: Mapping[ReasoningEffort, ThinkingEffort] = field(
        default_factory=dict[ReasoningEffort, ThinkingEffort]
    )
    vision: bool = False
    max_images: int | None = None
    thinking: ReasoningEffort | Literal[False] = False


def model_max_images(model: LlmModelSettings) -> int | None:
    """The gateway's image cap of *model*, the one of ``multimodal`` where it sets none."""
    return model.max_images or settings.multimodal.max_images


def run_max_images(llm: LlmConfig | None) -> int | None:
    """The image cap of a run's model, the one of ``multimodal`` without a model config."""
    return settings.multimodal.max_images if llm is None else llm.max_images


def _resolve(
    model_id: str,
    *,
    thinking: ReasoningEffort | Literal[False] = False,
    max_tokens: int | None = None,
) -> LlmConfig:
    """The catalog model *model_id* reasoning with *thinking*, its completion capped by *max_tokens* too."""
    model = settings.llm.models[model_id]
    caps = [cap for cap in (model.max_tokens, max_tokens) if cap is not None]

    return LlmConfig(
        model=model.model,
        provider=settings.llm.providers[model.provider],
        family=model.family,
        context_window=model.context_window,
        max_tokens=min(caps, default=None),
        reasoning=None if model.reasoning is None else tuple(model.reasoning),
        reasoning_overrides=model.reasoning_overrides,
        vision=model.vision,
        max_images=model_max_images(model),
        thinking=thinking,
    )


def chat_llm(
    model_id: str | None = None, thinking: ReasoningEffort = DEFAULT_REASONING_EFFORT
) -> LlmConfig | None:
    """The chat model *model_id*, the default one for ``None``, reasoning with *thinking*, none without a catalog.

    The model of everything that answers the user, so a turn, its subagents,
    its compaction, and the case base and ``complete`` work it triggers.
    """
    model_id = model_id or settings.llm.default

    return None if model_id is None else _resolve(model_id, thinking=thinking)


def turn_llm(llm: LlmConfig | None) -> LlmConfig | None:
    """The model of a run, the default chat model where it names none, as on MCP."""
    return llm or chat_llm()


def aux_llm() -> LlmConfig | None:
    """The model of one-off utility work, reasoning off and capped to ``aux_max_tokens``, none without a catalog.

    Only for work beside answering the user, such as captioning an image and
    titling a conversation.
    """
    model_id = settings.llm.aux or settings.llm.default

    return (
        None
        if model_id is None
        else _resolve(model_id, max_tokens=settings.llm.aux_max_tokens)
    )


def background_llm() -> LlmConfig | None:
    """The aux model for the work nobody asked for, none while ``llm.aux`` is unset.

    Setting ``llm.aux`` opts into that work, such as titling a new conversation
    and describing the images a conversion extracts, which would otherwise
    spend the chat model.
    """
    return None if settings.llm.aux is None else aux_llm()


def decision_llm() -> LlmConfig | None:
    """The model answering yes/no questions with a probability, none while ``llm.decision`` is unset.

    Setting ``llm.decision`` opts into the work that needs it, such as the
    ``markdown-coref`` chunker, on a chat model or a decision model alike.
    """
    model_id = settings.llm.decision

    return None if model_id is None else _resolve(model_id)


def stt_llm() -> LlmConfig | None:
    """The transcription model, none while ``llm.stt`` is unset."""
    model_id = settings.llm.stt

    return None if model_id is None else _resolve(model_id)


def clamp_reasoning(
    effort: ReasoningEffort, levels: tuple[ReasoningEffort, ...] | None
) -> ReasoningEffort | None:
    """The level of *levels* nearest *effort*, the lower on a tie, none for no levels.

    Unset *levels* accept any effort, so *effort* passes through unchanged.

    >>> clamp_reasoning("high", ("low", "medium"))
    'medium'
    >>> clamp_reasoning("medium", ("low", "high"))
    'low'
    >>> clamp_reasoning("low", ()) is None
    True
    >>> clamp_reasoning("high", None)
    'high'
    """
    if levels is None:
        return effort

    rank = REASONING_EFFORTS.index

    return min(
        levels,
        key=lambda level: (abs(rank(level) - rank(effort)), rank(level)),
        default=None,
    )
