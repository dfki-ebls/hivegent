"""Case bases of the workspaces and the models they are bound to.

The tool and the routes rank through :mod:`hivegent.tools.cases`, and this
module holds what needs the app.  Reading a case base never writes, its
configuration is inferred in memory.

The host's models are bound by name: the embedding model of the search index
as the embedder `hivegent`, the chat model as the language model
`hivegent`, and the ``llm.decision`` model as `hivegent-decision`
(:data:`~hivegent.tools.cases.EMBEDDER`,
:data:`~hivegent.tools.cases.LANGUAGE_MODEL`,
:data:`~hivegent.tools.cases.DECISION_MODEL`).  :data:`CASE_BASES` keeps the
case bases loaded last, each with the vectors of its cases.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from exar.pydantic_ai import Generation
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from .config import settings
from .llm import (
    MODEL_RETRIES,
    decision_model,
    model_from_config,
    thinking_model_settings,
)
from .llm_config import LlmConfig, decision_llm, turn_llm
from .retrieval import embedding_func
from .tools.cases import (
    DECISION_MODEL,
    EMBEDDER,
    LANGUAGE_MODEL,
    Bindings,
    CaseBaseCache,
)

__all__ = ["CASE_BASES", "bindings"]

CASE_BASES = CaseBaseCache()
"""The case bases loaded last, shared by the tool and the routes, see :class:`CaseBaseCache`."""


def _chat_model(config: LlmConfig) -> tuple[Model, ModelSettings]:
    return model_from_config(config), thinking_model_settings(config)


@dataclass(frozen=True, slots=True)
class _CaseModel:
    """The model of *config* as a cbrkit language model, built by *build* only once a case base calls it.

    Most uses of a case base only rank with the embedder, so they build no model.
    """

    config: LlmConfig
    build: Callable[[LlmConfig], tuple[Model, ModelSettings]]

    async def __call__[T](
        self, prompts: Sequence[str], response_type: type[T], /
    ) -> Sequence[T]:
        model, model_settings = self.build(self.config)
        generate = Generation(
            model=model,
            settings=model_settings,
            concurrency=settings.llm.case_concurrency,
            name="case-base",
            capabilities=[MODEL_RETRIES],
        )

        return await generate(prompts, response_type)


async def bindings(llm: LlmConfig | None = None) -> Bindings:
    """The embedding model and, if configured, the model of *llm* and the decision model by the names configurations use.

    *llm* is the model of the turn asking, the default chat model without one.
    """
    models: dict[str, _CaseModel] = {}

    if (config := turn_llm(llm)) is not None:
        models[LANGUAGE_MODEL] = _CaseModel(config, _chat_model)

    if (decision := decision_llm()) is not None:
        models[DECISION_MODEL] = _CaseModel(decision, decision_model)

    return Bindings({EMBEDDER: await embedding_func()}, models)
