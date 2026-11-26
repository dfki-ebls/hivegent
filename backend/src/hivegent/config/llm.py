"""Settings of the LLM provider and model catalog and of the agents using it."""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self, TypedDict
from urllib.parse import urljoin

from pydantic import (
    Field,
    PositiveFloat,
    PositiveInt,
    SecretStr,
    model_validator,
    with_config,
)
from pydantic_ai.settings import ThinkingEffort

from ..security import OperatorUrl
from ..shared.formats import BinaryContentMode
from .common import CatalogId, HttpOperatorUrl, Section, unknown_reference

__all__ = [
    "DEFAULT_REASONING_EFFORT",
    "FAMILY_PROVIDERS",
    "OPENAI_BASE_URL",
    "REASONING_EFFORTS",
    "InferenceProvider",
    "LlmModelSettings",
    "LlmProviderSettings",
    "LlmSettings",
    "ModelFamily",
    "MultimodalSettings",
    "ReasoningEffort",
    "SubagentSettings",
    "ThinkingBudgets",
]

OPENAI_BASE_URL = OperatorUrl("https://api.openai.com/v1")
"""Where the OpenAI API is served, for a model that names no endpoint."""


class InferenceProvider(StrEnum):
    """Inference server providing the OpenAI-compatible LLM endpoint.

    ``openai`` is OpenAI's own API, ``openai-compatible`` any other server
    that keeps to its contract, and the others name a self-hosted runtime.
    """

    LLAMA_CPP = "llama.cpp"
    VLLM = "vllm"
    SGLANG = "sglang"
    OLLAMA = "ollama"
    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai-compatible"


FAMILY_PROVIDERS = frozenset(
    {
        InferenceProvider.LLAMA_CPP,
        InferenceProvider.SGLANG,
        InferenceProvider.OPENAI_COMPATIBLE,
    }
)
"""The runtimes whose model profile a model's ``family`` selects.

The others profile a model by its served name, OpenAI's API by its own
models, and vLLM and Ollama by the families pydantic-ai knows them to serve.
"""


class ModelFamily(StrEnum):
    """Model family an endpoint of ``FAMILY_PROVIDERS`` serves, which selects its model profile."""

    LLAMA = "llama"
    GEMMA = "gemma"
    QWEN = "qwen"
    DEEPSEEK = "deepseek"
    MISTRAL = "mistral"
    COHERE = "cohere"
    GPT_OSS = "gpt-oss"
    GLM = "glm"


@with_config(extra="forbid")
class ThinkingBudgets(TypedDict):
    """Reasoning token cap per generic effort level, all three required."""

    low: PositiveInt
    medium: PositiveInt
    high: PositiveInt


type ReasoningEffort = Literal["low", "medium", "high"]
"""Reasoning effort accepted from the API, a subset of pydantic-ai's levels."""

REASONING_EFFORTS: tuple[ReasoningEffort, ...] = ("low", "medium", "high")
"""Every reasoning effort, from the least to the most."""

DEFAULT_REASONING_EFFORT: ReasoningEffort = "medium"
"""The effort of a request that names none."""


class LlmProviderSettings(Section):
    """An OpenAI-compatible endpoint the catalog's models are served from.

    ``api_key`` is a secret, so it comes from the credential
    ``hivegent.LLM__PROVIDERS__<ID>__API_KEY`` (or the matching variable)
    rather than the TOML file.  ``inference_provider`` names the runtime behind
    ``base_url``, from which the request fields and model-profile overrides it
    needs are derived.  It defaults to ``openai``, the strict reading: no
    non-standard request fields and no profile overrides, the only safe
    assumption for an endpoint that rejects what it does not know.
    """

    base_url: HttpOperatorUrl
    api_key: SecretStr | None = None
    inference_provider: InferenceProvider = InferenceProvider.OPENAI

    def url(self, path: str) -> str:
        """*path* resolved against ``base_url`` like a link, below it if relative and on its origin if absolute.

        >>> provider = LlmProviderSettings(base_url=OperatorUrl("https://llm.test/v1"))
        >>> provider.url("rerank"), provider.url("/llmhop/proxy/watermark/detect")
        ('https://llm.test/v1/rerank', 'https://llm.test/llmhop/proxy/watermark/detect')
        """
        return urljoin(f"{self.base_url.rstrip('/')}/", path)


class LlmModelSettings(Section):
    """One model of the catalog, which a chat request may name by its id.

    ``provider`` is the id of the endpoint serving it and ``model`` its served
    name there.  ``label`` is what the model selector shows, its id when empty.
    ``max_tokens`` caps a completion, ``reasoning`` lists the efforts a request
    may pick, the nearest of them replacing any other and an empty list turning
    thinking off, while unset takes any effort unchanged, and ``vision`` admits image attachments.  ``max_images`` is
    the gateway's per-request image cap for the model, the one of
    ``multimodal`` when unset.
    ``reasoning_overrides`` sends another effort for a requested one, the one
    the model implements in its place, such as Qwen3.8's ``xhigh`` for
    ``high``, while the thinking budget stays keyed on the requested effort.  ``chat`` offers the
    model to users, off for one that only fills a role such as transcription.
    ``family`` selects the model profile on an endpoint of
    ``FAMILY_PROVIDERS``, which cannot tell it from the served name, and leaves
    the one of its ``<vendor>/`` prefix or the generic one when unset.
    """

    provider: CatalogId
    model: str
    label: str = ""
    context_window: PositiveInt | None = None
    max_tokens: PositiveInt | None = None
    reasoning: list[ReasoningEffort] | None = None
    reasoning_overrides: dict[ReasoningEffort, ThinkingEffort] = {}
    vision: bool = False
    max_images: PositiveInt | None = None
    chat: bool = True
    family: ModelFamily | None = None


class LlmSettings(Section):
    """The model catalog and the limits of an agent run.

    ``providers`` and ``models`` are the catalog, keyed by id.  The roles name
    models by id: ``default`` answers a chat that names none and drives
    everything else that answers the user, subagents, compaction, case base
    refinement, and a program's ``complete``, which reuse the chat's model to
    keep its prompt cache.  Subagents and case base refinement also reason
    with the chat's effort, compaction never reasons, and a program's
    ``complete`` reasons only at the effort the program asks for.  ``aux`` powers only one-off
    utility work with reasoning off, document conversion, alt-text and
    caption generation, titles, and LLM-guided chunking, so it must be small,
    fast, and vision-capable, and it falls back to ``default``.  Setting it
    also runs that work unasked where it pays off, titling a new conversation
    and describing the images a conversion stores, which only a request does
    otherwise.  ``stt`` names an audio transcription
    model (e.g. ``whisper-1``), which backs the speech input fallback for
    browsers without a working Web Speech API, disabled while it is unset.

    ``aux_max_tokens`` bounds the one-shot aux workloads below the aux
    model's own ``max_tokens``: their outputs are short and the aux model is
    usually small-context, so an unbounded completion can fill the window
    before any answer is emitted.

    ``thinking_budget_tokens`` caps reasoning per request on llama.cpp and
    vLLM, keyed by the generic effort level before any model-specific mapping.
    Both force the reasoning block closed at the cap without truncating the
    answer.  llama.cpp honours it only while ``--reasoning-budget`` remains at
    its -1 default, and vLLM needs a reasoning parser and runner with
    thinking-budget support.

    The remaining knobs bound an agent run.  ``request_timeout_seconds`` is the
    per-request timeout applied to every model call (as ``ModelSettings.timeout``):
    for a non-streaming call it caps the whole call, and on a stream it detects a
    hung server (no bytes for that long). Continuous streaming resets it, so it
    does not cap total generation time.  ``tool_timeout_seconds`` bounds a single
    tool execution; on expiry pydantic-ai returns a retry prompt to the model, so
    a wedged tool (a stuck subprocess, query, or fetch) cannot stall a turn. Set
    it generously so a legitimately slow tool (a large-document conversion)
    still fits, or ``None`` to disable.
    ``request_limit`` and ``tool_calls_limit`` bound how many model requests and
    tool calls a turn may make, the main agent's shared with the completions of
    its programs and with its subagents, which all run on the same usage
    accumulator, so an agentic loop terminates on a budget rather than at
    pydantic-ai's implicit default of 50 requests.  The request
    limit is what bounds a turn, sized for a long task that compacts along the
    way, and ``tool_calls_limit`` defaults to ``None``, uncapped, since one
    request may issue many parallel calls, so a cap below a multiple of the
    request limit would end a turn first, and with no warning.  ``retries`` is
    the budget for re-prompting the model when a tool raises ``ModelRetry`` or
    output validation fails (applied to both), counted per tool as failures in
    a row and spent against ``request_limit``.  A correctable failure is how a
    run legitimately converges (``run_python`` meets Monty's subset of the
    standard library by trying an import, a query meets a column type by
    casting it), so a small budget abandons a turn the next call would have got
    right, while a model stuck repeating a failing call ends its turn long
    before the request limit would.

    ``caption_concurrency`` caps how many image-caption calls to the ``aux`` model
    a single document issues at once; an image-heavy document would otherwise
    fan out one concurrent vision request per unique image, tripping provider
    rate limits so retries make it slower, not faster.  ``case_concurrency``
    does the same for the model calls of one case base refinement.

    ``tool_output_max_chars`` is the most plain text
    (LLM-facing) any tool return shows the model.  A larger one is saved whole
    under the conversation's ``/tmp/.tool-results/`` and shown as its head and
    tail around a note naming the file, or only clamped to them where the run
    has no ``/tmp``, and a retry prompt is clamped to it too.
    ``tool_data_max_bytes`` is the most structured data (frontend-facing, as
    JSON) a tool return streams and stores, and a larger one is dropped.
    """

    providers: dict[CatalogId, LlmProviderSettings] = {}
    models: dict[str, LlmModelSettings] = {}
    default: str | None = None
    aux: str | None = None
    stt: str | None = None
    aux_max_tokens: int | None = 8192
    thinking_budget_tokens: ThinkingBudgets = {
        "low": 4096,
        "medium": 16384,
        "high": 32768,
    }
    request_timeout_seconds: float = 600.0
    tool_timeout_seconds: float | None = 600.0
    request_limit: int = 500
    tool_calls_limit: int | None = None
    caption_concurrency: PositiveInt = 4
    case_concurrency: PositiveInt = 4
    tool_output_max_chars: int = 60_000
    tool_data_max_bytes: int = 1_000_000
    retries: int = 20

    @model_validator(mode="after")
    def _check_catalog(self) -> Self:
        """Refuse a catalog whose references dangle, so a typo stops the boot."""
        for model_id, model in self.models.items():
            if model.provider not in self.providers:
                raise unknown_reference("Model", model_id, "provider")

            runtime = self.providers[model.provider].inference_provider

            if model.family is not None and runtime not in FAMILY_PROVIDERS:
                raise ValueError(
                    f"Model {model_id!r} names a family, which its {runtime} provider ignores"
                )

        for role in ("default", "aux", "stt"):
            model_id = getattr(self, role)
            if model_id is not None and model_id not in self.models:
                raise unknown_reference("Role", role, "model")

        if self.models and self.default is None:
            raise ValueError("llm.default must name a model of the catalog")

        if self.default is not None and not self.models[self.default].chat:
            raise ValueError("llm.default must name a chat model")

        return self


class SubagentSettings(Section):
    """The subagents the main agent of a chat turn may delegate to with pydantic-ai-harness.

    ``enabled`` offers the main agent the harness's delegation tools over the
    subagents.  ``timeout_seconds`` bounds one delegation, ``None`` leaving it
    unbounded, and its expiry reaches the main agent as a note in place of the
    subagent's result.  ``max_steps`` is how many model requests one
    delegation makes before it is told to report what it found.  ``max_depth`` is how deeply subagents nest below the
    main agent, 1 for its own subagents alone, ``max_running`` how many tasks
    run at once, and ``max_cost`` the cost in US dollars a turn's agents and
    the subagents it started may spend together, ``None`` for no bound.  It counts only the usage of models
    pydantic-ai can price, so the request limit alone bounds a self-hosted one.
    """

    enabled: bool = True
    timeout_seconds: PositiveFloat | None = 600.0
    max_steps: PositiveInt = 30
    max_depth: PositiveInt = 1
    max_running: PositiveInt = 8
    max_cost: Annotated[Decimal, Field(gt=0)] | None = None


class MultimodalSettings(Section):
    """How binary content reaches the chat model.

    ``binary_content`` governs the agent's binary reader
    (``read_binary_document``).  ``images`` (the default) rasterises PDFs
    to page images, the only multimodal content OpenAI-compatible vision
    servers (vLLM, SGLang, ...) accept; ``native`` forwards them as
    ``application/pdf`` for providers with first-class document
    understanding (OpenAI, Anthropic).  Set it to match the capabilities
    of the catalog's chat models.

    ``max_images`` sets the gateway's per-request image cap for every model
    of the catalog that sets none of its own.
    ``None`` leaves only the reader's page and frame budgets in effect.

    Ad-hoc chat attachments need no such policy: the composer accepts
    images alone (:data:`~hivegent.shared.formats.INGESTIBLE_IMAGE_MEDIA_TYPES`),
    which every vision backend ingests identically.

    Configurable via ``HIVEGENT_MULTIMODAL__BINARY_CONTENT`` and
    ``HIVEGENT_MULTIMODAL__MAX_IMAGES``.
    """

    binary_content: BinaryContentMode = BinaryContentMode.IMAGES
    max_images: PositiveInt | None = None
