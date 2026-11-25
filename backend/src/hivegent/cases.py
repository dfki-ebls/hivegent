"""Case bases of the workspaces and the models they are bound to.

The tool and the routes rank through :mod:`hivegent.tools.cases`, and this
module holds what needs the app.  Reading a case base never writes, its
configuration is inferred in memory.

The host's models are bound by name: the embedding model of the search index
as the embedder `hivegent` and the chat model as the language model
`hivegent` (:data:`~hivegent.tools.cases.EMBEDDER`,
:data:`~hivegent.tools.cases.LANGUAGE_MODEL`).  :data:`CASE_BASES` keeps the
case bases loaded last, each with the vectors of its cases.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from exar.agents import call
from exar.helpers import bounded_gather
from exar.pydantic_ai import PydanticAiAgent
from pydantic_ai import Agent

from .config import settings
from .llm import model_from_config, thinking_model_settings
from .llm_config import LlmConfig, turn_llm
from .retrieval import embedding_func
from .tools.cases import EMBEDDER, LANGUAGE_MODEL, Bindings, CaseBaseCache

__all__ = ["CASE_BASES", "bindings"]

CASE_BASES = CaseBaseCache()
"""The case bases loaded last, shared by the tool and the routes, see :class:`CaseBaseCache`."""


@dataclass(slots=True, frozen=True)
class _CaseModel:
    """The turn's model as a cbrkit language model, running the prompts concurrently, a bounded number at once."""

    config: LlmConfig

    async def __call__[T](
        self, prompts: Sequence[str], response_type: type[T], /
    ) -> Sequence[T]:
        case_agent = Agent(name="case-base", output_type=response_type)
        model = model_from_config(self.config)
        model_settings = thinking_model_settings(self.config)
        agent = PydanticAiAgent[str, T](
            run=lambda prompt: case_agent.run(
                prompt, model=model, model_settings=model_settings
            ),
            name="case-base",
        )

        return await bounded_gather(
            prompts,
            lambda text: call(agent, text),
            limit=settings.llm.case_concurrency,
        )


async def bindings(llm: LlmConfig | None = None) -> Bindings:
    """The embedding model and, if configured, the model of *llm* by the names configurations use.

    *llm* is the model of the turn asking, the default chat model without one.
    """
    config = turn_llm(llm)
    models = {} if config is None else {LANGUAGE_MODEL: _CaseModel(config)}

    return Bindings({EMBEDDER: await embedding_func()}, models)
