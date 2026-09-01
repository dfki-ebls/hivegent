"""The tool surface a sandboxed program is given.

The stub is the contract: it is what the model reads and what the type checker
enforces, so what it declares has to be the shape a program actually receives,
which is the serialised ``data`` channel and not the Python objects behind it.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, make_dataclass, replace
from pathlib import Path
from typing import Annotated, override

import pytest
from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from pydantic_monty import AsyncMonty

from hivegent.agents.capabilities import check_tool_settings, unlisted_tool_names
from hivegent.agents.common import UserDeps
from hivegent.agents.tools.compute import (
    INJECTABLE_TOOL_NAMES,
    sandbox_api_instructions,
    sandbox_surface,
)
from hivegent.agents.tools.explore import EXPLORE_FACTORIES
from hivegent.agents.tools.web import WEB_FACTORIES, web_enabled
from hivegent.config import settings
from hivegent.store import Casebase
from hivegent.tools.base import (
    AsyncTool,
    ToolOutput,
    ToolRetry,
    factory_tool_name,
    resolve_tool_cls,
)
from hivegent.tools.monty import monty_declarations, monty_surface
from hivegent.tools.python import RunPythonTool
from hivegent.tools.sink import OutputPathArg, RedirectedOutput, RedirectingTool
from hivegent.types import ToolsSpec

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
class _Search(RedirectingTool[list[Hit]]):
    """Search the things."""

    @override
    async def __call__(
        self, query: QueryArg, limit: LimitArg = 5, output_path: OutputPathArg = None
    ) -> ToolOutput[list[Hit] | RedirectedOutput]:
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


@pytest.fixture()
def deps(data_dir: Path) -> UserDeps:
    """A run with one personal workspace and nothing withheld."""
    _ = data_dir

    return UserDeps(user_id="u", store=Casebase.for_user("u"), mode="interactive")


class TestStub:
    def test_declares_the_serialised_shape_and_drops_the_redirect(self) -> None:
        stubs = monty_surface([_search], None).stubs

        # The model is a dict once it has crossed, arguments are keyword-only,
        # and the redirect is gone: a program holds the value already.
        assert "class Hit(TypedDict):" in stubs
        assert "    filename: str" in stubs
        assert "async def search(*, query: str, limit: int = 5) -> list[Hit]:" in stubs
        assert "output_path" not in stubs

    def test_carries_the_whole_description_of_a_tool_the_model_may_not_call(
        self,
    ) -> None:
        """A `sandbox_only` tool's guidance exists in the stub and nowhere else."""
        stubs = monty_surface([_search], None).stubs

        assert "Find matching records." in stubs
        assert "second paragraph" in stubs

    def test_a_tuple_is_a_list_and_a_defaulted_field_is_not_required(self) -> None:
        stubs = monty_surface([_wrap], None).stubs

        assert "    rows: list[list[str]]" in stubs
        assert "    missing: NotRequired[str | None]" in stubs
        assert stubs.index("class Nested") < stubs.index("class Wrapped")


class TestHostFunction:
    async def test_returns_the_structured_data_rather_than_the_text(self) -> None:
        call = monty_surface([_search], None).external_lookup["search"]

        # Keyword-only, as the declaration promises, and plain dicts back.
        assert await call(query="invoices", limit=1) == [
            {"filename": "a.md", "score": 0.5}
        ]

    async def test_a_retry_crosses_as_an_ordinary_exception(self) -> None:
        call = monty_surface([_search], None).external_lookup["search"]

        with pytest.raises(ValueError, match="give me a query"):
            await call(query="")

    async def test_validates_constraints_before_calling_the_tool(self) -> None:
        call = monty_surface([_search], None).external_lookup["search"]

        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            await call(query="invoices", limit=0)


def test_declarations_are_rendered_without_building_runtime_tools() -> None:
    """Prompt construction needs declarations but no dependency-bound tool."""

    def unavailable(_deps: None) -> _Search:
        raise AssertionError("a runtime tool was built")

    assert "async def unavailable" in monty_declarations([unavailable])


class TestGate:
    def test_a_disabled_tool_is_not_injected(self, deps: UserDeps) -> None:
        assert "query_table" in sandbox_surface(deps).external_lookup

        withheld = sandbox_surface(
            replace(deps, disabled_tools=frozenset({"query_table"}))
        )

        assert "query_table" not in withheld.external_lookup
        assert "query_table" not in withheld.stubs
        assert "search" in withheld.external_lookup

    def test_the_web_pair_follows_its_master_switch(self, deps: UserDeps) -> None:
        surface = sandbox_surface(deps)

        assert ("web_fetch" in surface.external_lookup) is web_enabled


class TestInsideTheSandbox:
    """The surface as a program actually meets it."""

    @pytest.fixture()
    async def tool(self) -> AsyncIterator[RunPythonTool]:
        async with AsyncMonty(min_processes=1) as pool:
            yield RunPythonTool(pool=pool, surface=monty_surface([_search], None))

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
    registered = {factory_tool_name(f) for f in (*EXPLORE_FACTORIES, *WEB_FACTORIES)}

    assert INJECTABLE_TOOL_NAMES <= registered
    assert INJECTABLE_TOOL_NAMES == {
        factory_tool_name(f)
        for f in (*EXPLORE_FACTORIES, *WEB_FACTORIES)
        if resolve_tool_cls(f).injectable
    }
    # The mount already is the read tools, so they are not handed over again.
    assert not INJECTABLE_TOOL_NAMES & {"grep", "read_document", "list_documents"}


class TestPlacement:
    """`sandbox_only` is the third answer to where a tool lives."""

    def test_a_sandbox_only_tool_keeps_its_function_and_loses_its_schema(
        self, deps: UserDeps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.tools, "sandbox_only", ["query_table"])

        assert "query_table" in unlisted_tool_names(ToolsSpec())
        assert "query_table" in sandbox_surface(deps).external_lookup

    def test_a_disabled_tool_reaches_neither_surface(
        self, deps: UserDeps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The operator's half holds on a deps that never carried it."""
        monkeypatch.setattr(settings.tools, "disabled", ["query_table"])

        assert "query_table" in unlisted_tool_names(ToolsSpec())
        assert "query_table" not in sandbox_surface(deps).external_lookup

    def test_the_request_withholds_a_tool_from_the_sandbox_too(
        self, deps: UserDeps
    ) -> None:
        withheld = replace(deps, disabled_tools=frozenset({"query_table"}))

        assert "query_table" not in sandbox_surface(withheld).external_lookup
        assert "query_table" in unlisted_tool_names(
            ToolsSpec(disabled_tools=["query_table"])
        )

    def test_a_tool_the_sandbox_cannot_take_fails_the_boot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.tools, "sandbox_only", ["write_document"])

        with pytest.raises(ValueError, match="run_python cannot be given"):
            check_tool_settings()


def test_the_mount_declares_its_own_open_and_the_model_never_sees_it() -> None:
    """`open` is a checker problem, and it belongs to the mount that provides it.

    Without the declaration every program that reads a document is rejected
    before it runs; in the prompt it would spend context telling the model what
    `open` is.  So the surface carries neither, and the tool joins the mount's
    half in when it hands the checker its stubs.
    """
    surface = monty_surface([_search], None)

    assert "def open(" not in surface.stubs
    assert "def open(" not in surface.declarations
    assert "async def search(*, query: str" in surface.declarations

    stubs = RunPythonTool(pool=None, surface=surface)._stubs()  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]

    assert "def open(" in stubs
    assert surface.stubs in stubs


def test_an_empty_surface_still_lets_a_program_open_a_document() -> None:
    """The mount's half of the stubs does not depend on a tool being injected."""
    assert "def open(" in RunPythonTool(pool=None)._stubs()  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]


def test_an_empty_surface_is_falsy() -> None:
    """A dataclass is otherwise always truthy, which is what the prompt asks."""
    assert not monty_surface([], None)
    assert monty_surface([_search], None)


def test_a_run_given_no_tool_opens_no_api_block(
    deps: UserDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prompt says nothing rather than declaring an empty surface.

    This is what the truthiness above is for: it silently never fired, so a run
    with every injectable tool withheld opened the block over nothing.
    """
    monkeypatch.setattr(settings.tools, "disabled", sorted(INJECTABLE_TOOL_NAMES))
    context = RunContext(deps=deps, model=TestModel(), usage=RunUsage())

    assert not sandbox_surface(deps)
    assert sandbox_api_instructions(context) == ""


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

    stubs = monty_surface([_wrap, _clash], None).stubs

    assert "class wrap_Wrapped(TypedDict):" in stubs
    assert "class clash_Wrapped(TypedDict):" in stubs
    assert "-> clash_Wrapped:" in stubs
