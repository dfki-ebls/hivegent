"""The tool surface a sandboxed program is given.

The stub is the contract: it is what the model reads and what the type checker
enforces, so what it declares has to be the shape a program actually receives,
which is the serialised ``data`` channel and not the Python objects behind it.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, make_dataclass, replace
from pathlib import Path
from typing import Annotated, Any, Literal, override

import pytest
from pydantic import BaseModel, Field, JsonValue
from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits
from pydantic_monty import AsyncMonty

from hivegent import llm
from hivegent.agents.capabilities import check_tool_settings, unlisted_tool_names
from hivegent.agents.common import Completions, UserDeps
from hivegent.agents.tools.explore import EXPLORE_FACTORIES
from hivegent.agents.tools.python import (
    INJECTABLE_TOOL_NAMES,
    SANDBOX_FUNCTION_NAMES,
    sandbox_instructions,
    sandbox_surface,
)
from hivegent.agents.tools.web import WEB_FACTORIES, web_enabled
from hivegent.config import settings
from hivegent.store import Casebase
from hivegent.tools.base import (
    AsyncTool,
    CallBudget,
    ToolOutput,
    ToolRetry,
    factory_tool_name,
    resolve_tool_cls,
)
from hivegent.tools.monty import (
    HostCalls,
    MontySurface,
    monty_declarations,
    monty_surface,
)
from hivegent.tools.python import RunPythonTool
from hivegent.types import ToolsSpec
from tests.helpers import LIMITS, run_context

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


@dataclass(slots=True, frozen=True)
class _Search(AsyncTool[list[Hit]]):
    """Search the things."""

    @override
    async def __call__(self, query: QueryArg, limit: LimitArg = 5) -> ToolOutput[list[Hit]]:
        """Find matching records.

        A second paragraph the stub must leave out, since the tool list already
        carries the full description.
        """
        if not query:
            raise ToolRetry("give me a query")

        return ToolOutput(data=[Hit(filename="a.md", score=0.5)][:limit])


@dataclass(slots=True, frozen=True)
class _Wrap(AsyncTool[Wrapped]):
    """Wrap the things."""

    @override
    async def __call__(self) -> ToolOutput[Wrapped]:
        """Return a nested record."""
        return ToolOutput(data=Wrapped(rows=(("a", "b"),), nested=(Nested("x"),)))


def _search(_deps: None) -> _Search:
    return _Search()


def _wrap(_deps: None) -> _Wrap:
    return _Wrap()


def _surface(*factories: Callable[[None], AsyncTool[Any]]) -> MontySurface:
    """The surface of *factories*, each built from no deps."""
    return monty_surface({factory: factory(None) for factory in factories})


@pytest.fixture()
def deps(data_dir: Path) -> UserDeps:
    """A run with one personal workspace and nothing withheld."""
    _ = data_dir

    return UserDeps(user_id="u", store=Casebase.for_user("u"), mode="interactive")


class TestStub:
    def test_declares_the_serialised_shape(self) -> None:
        stubs = _surface(_search).stubs

        # The model is a dict once it has crossed and arguments are keyword-only.
        assert "class Hit(TypedDict):" in stubs
        assert "    filename: str" in stubs
        assert "async def search(*, query: str, limit: int = 5) -> list[Hit]:" in stubs

    def test_carries_the_whole_description_of_a_tool_the_model_may_not_call(
        self,
    ) -> None:
        """A `sandbox_only` tool's guidance exists in the stub and nowhere else."""
        stubs = _surface(_search).stubs

        assert "Find matching records." in stubs
        assert "second paragraph" in stubs

    def test_a_tuple_is_a_list_and_a_defaulted_field_is_not_required(self) -> None:
        stubs = _surface(_wrap).stubs

        assert "    rows: list[list[str]]" in stubs
        assert "    missing: NotRequired[str | None]" in stubs
        assert stubs.index("class Nested") < stubs.index("class Wrapped")


def _bound(name: str) -> Callable[..., Awaitable[JsonValue]]:
    """The function *name* of the search surface, as one program is handed it."""
    return HostCalls(CallBudget(10)).bind(_surface(_search))[name]


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


def test_declarations_are_rendered_without_building_runtime_tools() -> None:
    """Prompt construction needs declarations but no dependency-bound tool."""

    def unavailable(_deps: None) -> _Search:
        raise AssertionError("a runtime tool was built")

    assert "async def unavailable" in monty_declarations([unavailable])


class TestGate:
    def test_a_disabled_tool_is_not_injected(self, deps: UserDeps) -> None:
        assert "query_table" in sandbox_surface(run_context(deps)).functions

        withheld = sandbox_surface(
            run_context(replace(deps, disabled_tools=frozenset({"query_table"})))
        )

        assert "query_table" not in withheld.functions
        assert "query_table" not in withheld.stubs
        assert "search" in withheld.functions

    def test_the_web_pair_follows_its_master_switch(self, deps: UserDeps) -> None:
        surface = sandbox_surface(run_context(deps))

        assert ("web_fetch" in surface.functions) is web_enabled


class TestInsideTheSandbox:
    """The surface as a program actually meets it."""

    @pytest.fixture()
    async def tool(self) -> AsyncIterator[RunPythonTool]:
        async with AsyncMonty(min_processes=1) as pool:
            yield RunPythonTool(pool=pool, changeset_limits=LIMITS, surface=_surface(_search))

    async def test_a_program_awaits_the_call_and_works_on_the_whole_result(
        self, tool: RunPythonTool
    ) -> None:
        """The composition the surface exists for: call and work are one program.

        A tool call would have shown the model the budgeted rendering, which it
        would then have had to read back before it could compute anything.
        """
        result = await tool(
            "hits = await search(query='inv')\n[h['filename'] for h in hits]"
        )

        assert result.data.result == "['a.md']"

    async def test_host_calls_are_capped_and_recorded(self, tool: RunPythonTool) -> None:
        program = """
await search(query='a')
await search(query='b')
try:
    await search(query='c')
except RuntimeError as exc:
    refused = str(exc)
refused
"""
        result = await replace(tool, max_host_calls=2)(program)

        assert "limit of 2 host function calls" in str(result.data.result)
        assert [call.arguments for call in result.data.calls] == ["query='a'", "query='b'"]
        assert result.data.calls[0].result == "[{'filename': 'a.md', 'score': 0.5}]"

    async def test_a_failure_lists_the_calls_that_finished(self, tool: RunPythonTool) -> None:
        with pytest.raises(ToolRetry, match=r"1 host call finished(.|\n)*search\(query='a'\) -> \["):
            await tool("await search(query='a')\n1 / 0")

    async def test_type_checking_rejects_a_misread_field_before_the_run(
        self, tool: RunPythonTool
    ) -> None:
        checked = replace(tool, type_check=True)

        with pytest.raises(ToolRetry, match="filenam"):
            await checked("hits = await search(query='inv')\nhits[0]['filenam']")


def test_the_injectable_set_is_derived_from_what_registers_the_tools() -> None:
    """One list, registered and filtered, so the two cannot drift.

    `Tool.injectable` is a property of the tool, so a factory renamed or a
    feature switched off moves both the tool list and the sandbox together —
    which is why nothing has to check that an injectable name is registered.
    """
    assert INJECTABLE_TOOL_NAMES == {
        factory_tool_name(f)
        for f in (*EXPLORE_FACTORIES, *WEB_FACTORIES)
        if resolve_tool_cls(f).injectable
    }
    # Only a program is handed `complete`, so `sandbox_only` cannot name it.
    assert SANDBOX_FUNCTION_NAMES - INJECTABLE_TOOL_NAMES == {"complete"}
    # The mount already is the read tools, so they are not handed over again.
    assert not INJECTABLE_TOOL_NAMES & {"grep", "read_document", "list_documents"}


class TestPlacement:
    """`sandbox_only` is the third answer to where a tool lives."""

    def test_a_sandbox_only_tool_keeps_its_function_and_loses_its_schema(
        self, deps: UserDeps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.tools, "sandbox_only", ["query_table"])

        assert "query_table" in unlisted_tool_names(ToolsSpec())
        assert "query_table" in sandbox_surface(run_context(deps)).functions

    def test_a_disabled_tool_reaches_neither_surface(
        self, deps: UserDeps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The operator's half holds on a deps that never carried it."""
        monkeypatch.setattr(settings.tools, "disabled", ["query_table"])

        assert "query_table" in unlisted_tool_names(ToolsSpec())
        assert "query_table" not in sandbox_surface(run_context(deps)).functions

    def test_the_request_withholds_a_tool_from_the_sandbox_too(
        self, deps: UserDeps
    ) -> None:
        withheld = replace(deps, disabled_tools=frozenset({"query_table"}))

        assert "query_table" not in sandbox_surface(run_context(withheld)).functions
        assert "query_table" in unlisted_tool_names(
            ToolsSpec(disabled_tools=["query_table"])
        )

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
    surface = _surface(_search)

    assert "def open(" not in surface.stubs
    assert "def open(" not in surface.declarations
    assert "async def search(*, query: str" in surface.declarations

    stubs = RunPythonTool(pool=None, changeset_limits=LIMITS, surface=surface)._stubs()  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]

    assert "def open(" in stubs
    assert surface.stubs in stubs


def test_an_empty_surface_still_lets_a_program_open_a_document() -> None:
    """The mount's half of the stubs does not depend on a tool being injected."""
    assert "def open(" in RunPythonTool(pool=None, changeset_limits=LIMITS)._stubs()  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]


def test_an_empty_surface_is_falsy() -> None:
    """A dataclass is otherwise always truthy, which is what the prompt asks."""
    assert not _surface()
    assert _surface(_search)


def test_a_run_given_no_tool_opens_no_api_block(
    deps: UserDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prompt says nothing rather than declaring an empty surface.

    This is what the truthiness above is for: it silently never fired, so a run
    with every injectable tool withheld opened the block over nothing.
    """
    monkeypatch.setattr(
        settings.tools, "disabled", sorted(SANDBOX_FUNCTION_NAMES)
    )
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

    @dataclass(slots=True, frozen=True)
    class _Clash(AsyncTool[other]):  # pyright: ignore[reportInvalidTypeForm]  # ty: ignore[invalid-type-form]
        """Clash."""

        @override
        async def __call__(self) -> ToolOutput[other]:  # pyright: ignore[reportInvalidTypeForm]  # ty: ignore[invalid-type-form]
            """Return the other record."""
            return ToolOutput(data=other(1))

    def _clash(_deps: None) -> _Clash:
        return _Clash()

    stubs = _surface(_wrap, _clash).stubs

    assert "class wrap_Wrapped(TypedDict):" in stubs
    assert "class clash_Wrapped(TypedDict):" in stubs
    assert "-> clash_Wrapped:" in stubs


class TestComplete:
    """The plain model call a program is handed and no tool list carries."""

    def test_is_declared_and_withheld_by_name(self, deps: UserDeps) -> None:
        declared = sandbox_instructions(run_context(deps))

        assert "async def complete(*, prompt: str) -> str:" in declared
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
            llm, "model_from_config", lambda _config: TestModel(custom_output_text="sunny")
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
            tool = RunPythonTool(
                pool=pool, changeset_limits=LIMITS, surface=sandbox_surface(context)
            )
            result = await tool(program)

        assert result.data.result == (
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
            tool = RunPythonTool(
                pool=pool,
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

        async def respond(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            started.append(_messages)

            if len(started) == 2:
                both_started.set()

            await both_started.wait()

            return ModelResponse(parts=[TextPart("sunny")])

        monkeypatch.setattr(llm, "model_from_config", lambda _config: FunctionModel(respond))
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
        monkeypatch.setattr(llm, "_completion_agent", Agent(output_type=Literal["ok"], retries=2))
        monkeypatch.setattr(
            llm, "model_from_config", lambda _config: TestModel(custom_output_args={"response": "invalid"})
        )
        context = run_context(deps)
        context.usage.requests = 1
        context.usage_limits = UsageLimits(request_limit=3)
        complete = sandbox_surface(context).functions["complete"]

        with pytest.raises(ToolRetry, match="keeps one for its response"):
            await complete(prompt="retry")

        assert context.usage.requests == 2
        assert deps.completions.pending == 0
