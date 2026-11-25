"""The tool surface a sandboxed program is given.

The stub is the contract: it is what the model reads and what the type checker
enforces, so what it declares has to be the shape a program actually receives,
which is the serialised ``data`` channel and not the Python objects behind it.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, make_dataclass, replace
from functools import partial
from pathlib import Path
from typing import Annotated

import pytest
from exar.tools import Retry, effect, tool_action
from exar.tools.batch import tool_return
from pydantic import BaseModel, Field, JsonValue
from pydantic_ai import Agent, ToolReturn
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits
from pydantic_monty import AsyncMonty

from hivegent import llm
from hivegent.agents.capabilities import check_tool_settings
from hivegent.agents.common import Completions, UserDeps, static_tools
from hivegent.agents.policy import listed
from hivegent.agents.tools.explore import explore_toolset
from hivegent.agents.tools.python import (
    INJECTABLE_TOOL_NAMES,
    SANDBOX_FUNCTION_NAMES,
    sandbox_instructions,
    sandbox_surface,
)
from hivegent.config import settings
from hivegent.store import User
from hivegent.tools.base import CallBudget, ToolFunc
from hivegent.tools.monty import (
    HostCalls,
    MontySurface,
    monty_surface,
)
from hivegent.tools.python import run_python
from hivegent.tools.workspace_os import MOUNT_STUB
from tests.helpers import LIMITS, catalog, run_context

QueryArg = Annotated[str, Field(description="What to look for.")]
LimitArg = Annotated[int, Field(ge=1)]


class Hit(BaseModel):
    """A result record that reaches the sandbox as a plain dict."""

    filename: str
    score: float


@dataclass(slots=True, frozen=True)
class Nested:
    """A record another record holds, so declaration order is exercised."""

    label: str


@dataclass(slots=True, frozen=True)
class Wrapped:
    """A result whose fields are tuples, which serialise as lists."""

    rows: tuple[tuple[str, ...], ...]
    nested: tuple[Nested, ...]
    missing: str | None = None


@effect("read")
async def search(query: QueryArg, limit: LimitArg = 5) -> ToolReturn[list[Hit]]:
    """Find matching records.

    A second paragraph the stub keeps for a tool the model may not call.
    """
    if not query:
        raise Retry("give me a query")

    if query == "slow":
        await asyncio.sleep(60)

    return tool_return([Hit(filename="a.md", score=0.5)][:limit])


@effect("read")
async def wrap() -> ToolReturn[Wrapped]:
    """Return a nested record."""
    return tool_return(Wrapped(rows=(("a", "b"),), nested=(Nested("x"),)))


def _surface(*funcs: ToolFunc) -> MontySurface:
    """The surface of *funcs*, none of which the model holds."""
    return monty_surface(funcs, frozenset())


@pytest.fixture()
def deps(data_dir: Path) -> UserDeps:
    """A run with one personal workspace and nothing withheld."""
    _ = data_dir

    return UserDeps(user=User("u"), mode="interactive")


class TestStub:
    def test_declares_the_serialised_shape(self) -> None:
        stubs = _surface(search).stubs

        # The model is a dict once it has crossed and arguments are keyword-only.
        assert "class Hit(TypedDict):" in stubs
        assert "    filename: str" in stubs
        assert "async def search(*, query: str, limit: int = 5) -> list[Hit]:" in stubs

    def test_carries_the_whole_description_of_a_tool_the_model_may_not_call(
        self,
    ) -> None:
        """A `sandbox_only` tool's guidance exists in the stub and nowhere else."""
        stubs = _surface(search).stubs

        assert "Find matching records." in stubs
        assert "second paragraph" in stubs

    def test_points_at_a_listed_tool_instead_of_repeating_it(self) -> None:
        """The model read a listed tool's documentation, so only its records stay."""
        declared = monty_surface([search], frozenset({"search"})).declarations

        assert (
            "async def search(*, query: str, limit: int = 5) -> list[Hit]:" in declared
        )
        assert "Your search tool" in declared
        assert "Find matching records." not in declared
        assert "What to look for." not in declared
        assert "class Hit(TypedDict):" in declared

    def test_a_tuple_is_a_list_and_a_defaulted_field_is_not_required(self) -> None:
        stubs = _surface(wrap).stubs

        assert "    rows: list[list[str]]" in stubs
        assert "    missing: NotRequired[str | None]" in stubs
        assert stubs.index("class Nested") < stubs.index("class Wrapped")


def _bound(name: str) -> Callable[..., Awaitable[JsonValue]]:
    """The function *name* of the search surface, as one program is handed it."""
    return HostCalls(CallBudget(10)).bind(_surface(search))[name]


class TestHostFunction:
    async def test_returns_the_structured_data_rather_than_the_text(self) -> None:
        call = _bound("search")

        # Keyword-only, as the declaration promises, and plain dicts back.
        assert await call(query="invoices", limit=1) == [
            {"filename": "a.md", "score": 0.5}
        ]

    async def test_a_retry_crosses_as_an_ordinary_exception(self) -> None:
        call = _bound("search")

        with pytest.raises(ValueError, match="give me a query"):
            await call(query="")

    async def test_validates_constraints_before_calling_the_tool(self) -> None:
        call = _bound("search")

        with pytest.raises(ValueError, match="greater than or equal to 1"):
            await call(query="invoices", limit=0)


class TestGate:
    def test_a_disabled_tool_is_not_injected(self, deps: UserDeps) -> None:
        assert "query_table" in sandbox_surface(run_context(deps)).functions

        withheld = sandbox_surface(
            run_context(replace(deps, disabled_tools=frozenset({"query_table"})))
        )

        assert "query_table" not in withheld.functions
        assert "query_table" not in withheld.stubs
        assert "search" in withheld.functions


class TestInsideTheSandbox:
    """The surface as a program actually meets it."""

    @pytest.fixture()
    async def build(self) -> AsyncIterator[Callable[..., ToolFunc]]:
        async with AsyncMonty(min_processes=1) as pool:
            yield partial(
                run_python,
                pool=lambda: pool,
                changeset_limits=LIMITS,
                surface=_surface(search),
            )

    async def test_a_program_awaits_the_call_and_works_on_the_whole_result(
        self, build: Callable[..., ToolFunc]
    ) -> None:
        """The composition the surface exists for: call and work are one program.

        A tool call would have shown the model the budgeted rendering, which it
        would then have had to read back before it could compute anything.
        """
        result = await build()(
            "hits = await search(query='inv')\n[h['filename'] for h in hits]"
        )

        assert result.metadata.result == "['a.md']"

    async def test_host_calls_are_capped_and_recorded(
        self, build: Callable[..., ToolFunc]
    ) -> None:
        program = """
await search(query='a')
await search(query='b')
try:
    await search(query='c')
except RuntimeError as exc:
    refused = str(exc)
refused
"""
        result = await build(max_host_calls=2)(program)

        assert "limit of 2 host function calls" in str(result.metadata.result)
        assert [call.arguments for call in result.metadata.calls] == [
            "query='a'",
            "query='b'",
        ]
        assert result.metadata.calls[0].result == "[{'filename': 'a.md', 'score': 0.5}]"

    async def test_a_call_past_the_time_limit_is_cut_off_for_the_program_to_finish(
        self, build: Callable[..., ToolFunc]
    ) -> None:
        program = """
results = []
for query in ['slow', 'a']:
    try:
        results.append(await search(query=query))
    except TimeoutError as exc:
        results.append(str(exc))
results
"""
        result = await build(max_wall_seconds=0.2)(program)

        assert str(result.metadata.result).count("reached its time limit") == 2
        assert [call.arguments for call in result.metadata.calls] == ["query='slow'"]

    async def test_a_failure_lists_the_calls_that_finished(
        self, build: Callable[..., ToolFunc]
    ) -> None:
        with pytest.raises(
            Retry, match=r"1 host call finished(.|\n)*search\(query='a'\) -> \["
        ):
            await build()("await search(query='a')\n1 / 0")

    async def test_type_checking_rejects_a_misread_field_before_the_run(
        self, build: Callable[..., ToolFunc]
    ) -> None:
        checked = build(type_check=True)

        with pytest.raises(Retry, match="filenam"):
            await checked("hits = await search(query='inv')\nhits[0]['filenam']")


def test_the_injectable_set_is_taken_from_the_tools_the_model_holds() -> None:
    """A program is handed the explore tools it needs, built from the list that registers them."""
    assert INJECTABLE_TOOL_NAMES <= {td.name for td in static_tools(explore_toolset)}
    # Only a program is handed `complete`, so `sandbox_only` cannot name it.
    assert SANDBOX_FUNCTION_NAMES - INJECTABLE_TOOL_NAMES == {"complete"}
    # The mount already is the read tools, so they are not handed over again.
    assert not INJECTABLE_TOOL_NAMES & {"grep", "read_document", "list_documents"}
    # A program looping over fetched links would carry data out by its URLs.
    assert not INJECTABLE_TOOL_NAMES & {"web_fetch", "web_search"}


class TestPlacement:
    """`sandbox_only` is the third answer to where a tool lives."""

    def test_a_sandbox_only_tool_keeps_its_function_and_loses_its_schema(
        self, deps: UserDeps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.tools, "sandbox_only", ["query_table"])

        assert deps.policy(tool_action("query_table", "read")) == "allow"
        assert not listed(deps.policy, tool_action("query_table", "read"))
        assert "query_table" in sandbox_surface(run_context(deps)).functions

    def test_a_disabled_tool_reaches_neither_surface(
        self, deps: UserDeps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The operator's half holds on a deps that never carried it."""
        monkeypatch.setattr(settings.tools, "disabled", ["query_table"])

        assert deps.policy(tool_action("query_table", "read")) == "deny"
        assert "query_table" not in sandbox_surface(run_context(deps)).functions

    def test_the_request_withholds_a_tool_from_the_sandbox_too(
        self, deps: UserDeps
    ) -> None:
        withheld = replace(deps, disabled_tools=frozenset({"query_table"}))

        assert "query_table" not in sandbox_surface(run_context(withheld)).functions
        assert withheld.policy(tool_action("query_table", "read")) == "deny"

    def test_a_tool_the_sandbox_cannot_take_fails_the_boot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.tools, "sandbox_only", ["write_document"])

        with pytest.raises(ValueError, match="the model and run_python share"):
            check_tool_settings()


def test_the_mount_declares_its_own_open_and_the_model_never_sees_it() -> None:
    """`open` is a checker problem, and it belongs to the mount that provides it.

    Without the declaration every program that reads a document is rejected
    before it runs; in the prompt it would spend context telling the model what
    `open` is.  So the surface carries neither, and the tool joins the mount's
    half in when it hands the checker its stubs.
    """
    surface = _surface(search)

    assert "def open(" not in surface.stubs
    assert "def open(" not in surface.declarations
    assert "async def search(*, query: str" in surface.declarations
    assert "def open(" in MOUNT_STUB


def test_an_empty_surface_is_falsy() -> None:
    """A dataclass is otherwise always truthy, which is what the prompt asks."""
    assert not _surface()
    assert _surface(search)


def test_a_run_given_no_tool_opens_no_api_block(
    deps: UserDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prompt says nothing rather than declaring an empty surface.

    This is what the truthiness above is for: it silently never fired, so a run
    with every injectable tool withheld opened the block over nothing.
    """
    monkeypatch.setattr(settings.tools, "disabled", sorted(SANDBOX_FUNCTION_NAMES))
    context = run_context(deps)

    assert not sandbox_surface(context)
    assert sandbox_instructions(context) == ""


def test_two_records_sharing_a_name_are_disambiguated() -> None:
    """Getting this wrong is silent, which is why it is not left to a name key.

    A renderer keyed on the bare class name declares the second tool with the
    first one's fields, so the program that reads its own result correctly is
    the one the type check turns away.  The upstream renderer prefixes each
    with its function's name instead.
    """
    other = make_dataclass("Wrapped", [("beta", int)], frozen=True, slots=True)

    @effect("read")
    async def clash() -> ToolReturn[other]:  # pyright: ignore[reportInvalidTypeForm]  # ty: ignore[invalid-type-form]
        """Return the other record."""
        return tool_return(other(1))

    stubs = _surface(wrap, clash).stubs

    assert "class wrap_Wrapped(TypedDict):" in stubs
    assert "class clash_Wrapped(TypedDict):" in stubs
    assert "-> clash_Wrapped:" in stubs


class TestComplete:
    """The plain model call a program is handed and no tool list carries."""

    @pytest.fixture(autouse=True)
    def models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "llm", catalog())

    def test_is_declared_and_withheld_by_name(self, deps: UserDeps) -> None:
        declared = sandbox_instructions(run_context(deps))

        assert "async def complete(" in declared
        assert "reasoning: Literal['low', 'medium', 'high'] | None = None" in declared
        assert "Use `complete` to classify" in declared
        assert "complete" in sandbox_surface(run_context(deps)).functions

        withheld = run_context(replace(deps, disabled_tools=frozenset({"complete"})))

        assert "complete" not in sandbox_surface(withheld).functions
        assert "`complete`" not in sandbox_instructions(withheld)

    async def test_a_program_gets_the_text_until_the_budget_is_spent(
        self, deps: UserDeps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The budget is the turn's, so its end is an exception the program catches."""
        monkeypatch.setattr(
            llm,
            "model_from_config",
            lambda _config: TestModel(custom_output_text="sunny"),
        )
        context = run_context(replace(deps, completions=Completions(max_calls=1)))
        program = """
first = await complete(prompt='weather?')
try:
    second = await complete(prompt='again?')
except ValueError as exc:
    second = str(exc)
[first, second]
"""

        async with AsyncMonty(min_processes=1) as pool:
            tool = run_python(
                pool=lambda: pool,
                changeset_limits=LIMITS,
                surface=sandbox_surface(context),
            )
            result = await tool(program)

        assert result.metadata.result == (
            "['sunny', 'complete may be called 1 times per turn and this turn "
            "has used them all. Finish without it.']"
        )
        assert context.usage.requests == 1

    async def test_the_turn_limit_ends_the_turn_even_when_caught(
        self, deps: UserDeps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running out of the shared budget is no program error, so no retry reports it."""

        async def spent(*_args: object, **_kwargs: object) -> str:
            raise UsageLimitExceeded("out of requests")

        monkeypatch.setattr(llm, "model_from_config", lambda _config: TestModel())
        monkeypatch.setattr(llm, "complete", spent)
        program = """
try:
    await complete(prompt='x')
except RuntimeError:
    pass
'done'
"""

        async with AsyncMonty(min_processes=1) as pool:
            tool = run_python(
                pool=lambda: pool,
                changeset_limits=LIMITS,
                surface=sandbox_surface(run_context(deps)),
            )

            with pytest.raises(UsageLimitExceeded):
                await tool(program)

    async def test_concurrent_programs_reserve_request_slots(
        self, deps: UserDeps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started: list[list[ModelMessage]] = []
        both_started = asyncio.Event()

        async def respond(
            _messages: list[ModelMessage], _info: AgentInfo
        ) -> ModelResponse:
            started.append(_messages)

            if len(started) == 2:
                both_started.set()

            await both_started.wait()

            return ModelResponse(parts=[TextPart("sunny")])

        monkeypatch.setattr(
            llm, "model_from_config", lambda _config: FunctionModel(respond)
        )
        context = run_context(deps)
        context.usage.requests = 1
        context.usage_limits = UsageLimits(request_limit=4)
        surfaces = [sandbox_surface(context), sandbox_surface(context)]
        functions = [
            HostCalls(CallBudget(10)).bind(surface)["complete"] for surface in surfaces
        ]

        async with asyncio.timeout(10):
            results = await asyncio.gather(
                *(functions[index % 2](prompt="weather?") for index in range(4)),
                return_exceptions=True,
            )

        assert results[:2] == ["sunny", "sunny"]
        assert all(isinstance(result, ValueError) for result in results[2:])
        assert len(started) == 2
        assert context.usage.requests == 3
        assert deps.completions.pending == 0

    async def test_retries_cannot_consume_reserved_slots(
        self, deps: UserDeps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unknown_tool(
            messages: list[ModelMessage], info: AgentInfo
        ) -> ModelResponse:
            return ModelResponse(parts=[ToolCallPart("missing", {})])

        monkeypatch.setattr(llm, "_completion_agent", Agent(retries=2))
        monkeypatch.setattr(
            llm, "model_from_config", lambda _config: FunctionModel(unknown_tool)
        )
        context = run_context(deps)
        context.usage.requests = 1
        context.usage_limits = UsageLimits(request_limit=3)
        complete = sandbox_surface(context).functions["complete"]

        with pytest.raises(Retry, match="keeps one for its response"):
            await complete(prompt="retry")

        assert context.usage.requests == 2
        assert deps.completions.pending == 0
