"""Settings of the embedding and reranking models."""

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import Discriminator, Field, PositiveInt, Tag
from pydantic_ai.embeddings import EmbeddingSettings as EmbeddingRequestSettings

from .common import ProviderEndpoint, Section

__all__ = [
    "EmbeddingSettings",
    "LocalEmbeddingSettings",
    "LocalRerankSettings",
    "RemoteEmbeddingSettings",
    "RemoteRerankSettings",
    "RerankSettings",
]


class _EmbeddingSettings(Section):
    """What the embedding models of every provider share, see ``EmbeddingSettings``."""

    model: str
    dimension: PositiveInt
    """The vector size the ``chunks.embedding`` column and every Alembic
    migration are built against, which must match ``model``.  A plain
    configured value, so importing the ORM never loads embedding weights.
    On a change, the boot resizes the live column and re-embeds every chunk."""
    query_prefix: str = ""
    """Prepended to every query, such as ``"query: "`` for e5 models."""
    document_prefix: str = ""
    """Prepended to every chunk text, such as ``"passage: "`` for e5 models.
    On a change, the boot re-embeds every chunk."""
    batch_size: PositiveInt
    """Most texts of one forward pass or request."""
    text_search_config: tuple[str, ...] = ("german", "english")
    """PostgreSQL FTS configurations for the sparse (keyword) channel.

    Drives both the ``chunks.tsv`` generated column and the query-side
    ``plainto_tsquery`` (passed to cbrkit as ``tsvector_config``), kept
    in lockstep through this single value.  The corpus is stemmed with
    every listed dictionary at once, ``["german", "english"]`` indexes
    the union ``to_tsvector('german', text) || to_tsvector('english',
    text)`` and queries it the same way, so German morphology (compounds,
    inflection) and English are both handled properly for the
    Germany-based, bilingual audience.  Use the single language-agnostic
    ``["simple"]`` (no stemming) instead when the language set is unknown.
    On a change, the boot recreates the generated column, which PostgreSQL
    fills from the text again.
    """


class LocalEmbeddingSettings(_EmbeddingSettings):
    """An embedding model run locally by sentence-transformers."""

    provider: Literal["sentence-transformers"] = "sentence-transformers"
    model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    dimension: PositiveInt = 384
    batch_size: PositiveInt = 32


class RemoteEmbeddingSettings(_EmbeddingSettings, ProviderEndpoint):
    """An embedding model served by an OpenAI-compatible provider."""

    provider: Literal["openai"]
    batch_size: PositiveInt = 128
    concurrency: PositiveInt = 4
    """Most requests at a time."""
    request: EmbeddingRequestSettings = {}
    """Passed to every request verbatim, such as ``truncate``, ``dimensions``,
    or ``extra_body`` (e.g. ``{"truncate_prompt_tokens": -1}`` for vLLM).
    Only OpenAI's own models are cut to their ``8192`` tokens client-side,
    since only their tokenizer is known, so a self-hosted server truncates
    over-long texts itself or refuses them."""


def _embedding_provider(
    value: Mapping[str, object] | LocalEmbeddingSettings | RemoteEmbeddingSettings,
) -> object:
    """The ``provider`` of an embedding section, ``sentence-transformers`` if it names none."""
    if isinstance(value, Mapping):
        return value.get("provider", "sentence-transformers")

    return value.provider


# A plain alias, since pydantic-settings parses the nested environment
# variables of a field only through it, not through a `type` statement.
EmbeddingSettings = Annotated[
    Annotated[LocalEmbeddingSettings, Tag("sentence-transformers")]
    | Annotated[RemoteEmbeddingSettings, Tag("openai")],
    Discriminator(_embedding_provider),
]
"""Embedding model configuration for dense retrieval, keyed on ``provider``.

Configurable via ``HIVEGENT_EMBEDDING__PROVIDER``,
``HIVEGENT_EMBEDDING__MODEL``, etc.
"""


class _RerankSettings(Section):
    """What the rerankers of every provider share, see ``RerankSettings``."""

    model: str = Field(min_length=1)
    candidate_multiplier: PositiveInt = 5
    """How many times the requested results the base retriever over-fetches
    for the reranker to rescore."""
    top_n: PositiveInt | None = None
    """How many of the best rescored candidates the reranker keeps, all if unset."""


class LocalRerankSettings(_RerankSettings):
    """A local cross-encoder, no external calls (e.g. ``BAAI/bge-reranker-v2-m3``)."""

    provider: Literal["sentence-transformers"]


class RemoteRerankSettings(_RerankSettings, ProviderEndpoint):
    """The ``rerank`` route of a provider, e.g. vLLM's."""

    provider: Literal["http"]


RerankSettings = Annotated[
    LocalRerankSettings | RemoteRerankSettings, Field(discriminator="provider")
]
"""Cross-encoder re-ranking applied after hybrid retrieval, keyed on ``provider``.

The reranker rescores the candidate pool, letting separately-stored caption
chunks compete on relevance rather than raw fusion score.  Disabled when
unset, to keep retrieval latency low, enabling trades runtime for ranking
quality.

Configurable via ``HIVEGENT_RERANK__PROVIDER``, ``HIVEGENT_RERANK__MODEL``,
etc.
"""
