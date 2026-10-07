"""Adapter exposing tool classes as host functions inside the Monty sandbox.

The mount already makes ``open`` and ``iterdir`` the read tools, ``re`` grep,
and ``json`` jq, which leaves exactly the tools whose answer no program can
compute for itself: retrieval needs the database, the web tools need the
network, and a spreadsheet needs a decoder Monty does not have.  Those are
injected here, so one program searches, queries, and counts in a single call
rather than spending a turn and a ``/tmp`` file per step.  Nothing that
mutates is injected and nothing needs to be: a program cannot stop to ask for
approval, and every function here is a read.

What crosses the boundary is the structured ``data`` channel, as the plain
objects the tool's declared result type serialises to.  That declared type is
also what the rendered stub names and what ``return_schema`` publishes, so what
a program receives and what it was told to expect are one description read
twice, and it stops at objects rather than going on to bytes a program would
only parse back.  The model-facing ``text`` channel stays behind, as its
budgets, truncation, and hints exist to fit a context window a program does not
have, and a program that wanted fewer rows can say so in the query.

The declarations are rendered by pydantic-ai's own
:class:`~pydantic_ai.function_signature.FunctionSignature`, which is what
``pydantic-ai-harness``'s code mode uses for the same purpose, so this module
supplies the two JSON schemas per tool and does none of the type rendering
itself.  That is worth more than it looks: the renderer states a defaulted
field as ``NotRequired``, carries each field's own description into the stub,
and disambiguates two tools whose result records share a class name by
prefixing each with its function's name — the last of which a name-keyed
renderer gets silently and catastrophically wrong, declaring the second tool
with the first one's fields so that reading its result correctly is the thing
the type check rejects.

The result is produced twice, exactly as the harness does it:
:attr:`MontySurface.declarations` for the model and :attr:`MontySurface.stubs`
for Monty's ``type_check_stubs``, so the shape promised and the shape enforced
are one text.  Rendering depends on the tools alone, so it is cached on them,
while the host functions are rebuilt per run because they close over the run's
deps.

A schema describes the serialised shape and not the Python type it started as:
``json_schema(mode="serialization")`` is what makes a ``tuple`` field a list and
a model a plain dict, which is what actually arrives inside the program.

Every program gets the functions bound to a fresh :class:`HostCalls`, which
applies one policy at the boundary to every one of them: the budget, the
errors a program may meet, and a short preview of each call once it returned
or raised.  The ``run_python`` card shows those previews, and a failed run
lists them so the next attempt can build on them instead of paying for the
same calls.
"""

import inspect
import reprlib
import types
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache, wraps
from typing import Any

from pydantic import JsonValue, ValidationError
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.function_signature import FunctionSignature
from pydantic_ai.tools import ToolDefinition

from ..converters.base import fenced_code_block
from .base import AsyncTool, AsyncToolFactory, CallBudget, ToolRetry, ToolSpec
from .formatting import render_arguments, truncate_line

__all__ = [
    "TURN_ENDING_ERRORS",
    "HostCall",
    "HostCalls",
    "MontySurface",
    "monty_declarations",
    "monty_surface",
]

TURN_ENDING_ERRORS: tuple[type[Exception], ...] = (UsageLimitExceeded,)
"""What a host function may raise that ends the turn rather than the program.

The turn's shared usage limit is spent for every run in it, so no rewrite of
the program could get past it.
"""

type _HostFunction = Callable[..., Awaitable[JsonValue]]
"""What one injected tool becomes, whose payload the rendered stub declares."""

_STUB_HEADER = "import asyncio\nfrom typing import Any, Literal, NotRequired, TypedDict"
"""What the harness puts at the head of its stubs, and for the same reasons."""

_CATALOG_BODY = "..."
"""What a declaration's body is in the catalog the model reads."""

_STUB_BODY = "raise NotImplementedError()"
"""What it is in the stubs the checker reads, as the harness spells it."""


_PREVIEW = reprlib.Repr(
    maxlevel=2,
    maxtuple=5,
    maxlist=5,
    maxarray=5,
    maxdict=5,
    maxset=5,
    maxfrozenset=5,
    maxdeque=5,
    maxstring=120,
    maxlong=40,
    maxother=120,
)
"""Shows the first few items of each container, two levels deep.

``reprlib`` stops at these limits while it builds the string, so a result
holding every row of a spreadsheet is never rendered in full just to be clipped.
"""

_PREVIEW_CHARS = 200
"""What one argument list, result, or error of a :class:`HostCall` keeps."""


def _preview(value: object) -> str:
    """Render *value* within :data:`_PREVIEW_CHARS`.

    >>> _preview([{"a": 1}] * 9)
    "[{'a': 1}, {'a': 1}, {'a': 1}, {'a': 1}, {'a': 1}, ...]"
    """
    return truncate_line(_PREVIEW.repr(value), _PREVIEW_CHARS)


@dataclass(slots=True, frozen=True)
class HostCall:
    """One call a program made to a host function, bounded to a preview."""

    function: str
    arguments: str
    """The keyword arguments, as the program spelled them."""

    result: str | None = None
    error: str | None = None
    """The exception the program met instead of a result."""

    def render(self) -> str:
        """The call as one line of a report.

        >>> HostCall("search", "query='x'", result="[]").render()
        "search(query='x') -> []"
        """
        outcome = f"error {self.error}" if self.error is not None else self.result

        return f"{self.function}({self.arguments}) -> {outcome}"


@dataclass(slots=True, frozen=True)
class MontySurface:
    """The host functions a program may call, and the stub declaring them."""

    functions: Mapping[str, _HostFunction] = types.MappingProxyType({})
    """Each function by name, unguarded until :meth:`HostCalls.bind` binds it."""

    declarations: str = ""
    """What the model is shown: the injected tools and the records they return.

    Empty when nothing is injected, where the prompt says nothing rather than
    opening an empty block.
    """

    stubs: str = ""
    """What the type checker is given, which the mount's own stub joins.

    It differs from :attr:`declarations` by what the checker needs and the model
    does not (the ``typing`` imports), and by the body of each declaration,
    which neither of them reads.
    """

    def __bool__(self) -> bool:
        """Whether anything was injected; a dataclass is otherwise always truthy."""
        return bool(self.declarations)


def _program_error(name: str, exc: Exception) -> Exception:
    """What a program meets for a failed call: a correction, or the bare type.

    >>> _program_error("complete", ToolRetry("give me a query"))
    ValueError('give me a query')
    >>> _program_error("complete", OSError("/srv/secret"))
    RuntimeError('complete raised OSError')
    """
    if isinstance(exc, ToolRetry | ValidationError):
        return ValueError(str(exc))

    return RuntimeError(f"{name} raised {type(exc).__name__}")


@dataclass(slots=True)
class HostCalls:
    """What one program asks of the host: its budget, its errors, and a record.

    One policy for every function :meth:`bind` hands a program.  A call is
    counted against the program's :attr:`budget` before its first await, so
    fanning out with ``asyncio.gather`` cannot exceed it.  A
    :class:`~hivegent.tools.base.ToolRetry` or an invalid argument reaches the
    program as a ``ValueError`` it may correct, and any other failure as a
    ``RuntimeError`` naming its type alone, since a host error may carry host
    details.  An exception of :data:`TURN_ENDING_ERRORS` is not the program's
    failure: the first one is kept in :attr:`ending` for the host to raise once
    the program stopped, and every later call is refused, since the turn it
    would spend on is over.
    """

    budget: CallBudget
    finished: list[HostCall] = field(default_factory=list)
    ending: Exception | None = None

    def bind(self, surface: MontySurface) -> dict[str, _HostFunction]:
        """The functions of *surface* for one program, each held to this policy."""
        return {
            name: self._guarded(name, function)
            for name, function in surface.functions.items()
        }

    def raise_ending(self) -> None:
        """Raise the turn-ending exception a call met, whatever the program made of it."""
        if self.ending is not None:
            raise self.ending

    def _guarded(self, name: str, function: _HostFunction) -> _HostFunction:
        """Wrap *function* to be counted before it runs and recorded once it finished."""

        @wraps(function)
        async def call(**kwargs: Any) -> JsonValue:
            if self.ending is not None:
                raise RuntimeError("The turn has ended, so no further host call runs.")

            self.budget.take(
                f"This program reached its limit of {self.budget.limit} host "
                "function calls. Batch what one call can take, such as several "
                "queries in one query_table, or move the rest into a second program."
            )

            arguments = render_arguments(kwargs, _PREVIEW_CHARS)

            try:
                result = await function(**kwargs)

            except TURN_ENDING_ERRORS as exc:
                self.ending = self.ending or exc
                raise

            except Exception as exc:
                error = _program_error(name, exc)
                preview = truncate_line(f"{type(error).__name__}: {error}", _PREVIEW_CHARS)
                self.finished.append(HostCall(name, arguments, error=preview))
                raise error from exc

            self.finished.append(HostCall(name, arguments, result=_preview(result)))

            return result

        return call


def _definition(factory: AsyncToolFactory[Any]) -> ToolDefinition:
    """Describe one tool the way the renderer wants it.

    A :class:`ToolDefinition` rather than a bare ``FunctionSignature``, since it
    is what pairs the name and description with the two schemas and caches the
    signature built from them, which is one invariant this module then does not
    have to keep by hand.
    """
    spec = ToolSpec.from_factory(factory)

    return ToolDefinition(
        name=spec.name,
        parameters_json_schema=spec.parameters_json_schema,
        description=spec.description,
        return_schema=spec.data_json_schema,
    )


@cache
def _rendered(factories: tuple[AsyncToolFactory[Any], ...]) -> tuple[str, str]:
    """The catalog and the stubs for these tools, rendered once per process.

    Cached because rendering is a pure function of the tools: a factory is a
    module-level function, the set of them is fixed at import, and only which
    of them are live varies per run.  Uncached this ran on every model request
    (the catalog is a dynamic instruction) and again on every ``run_python``
    call, for perhaps fifty milliseconds a turn of schema building that
    produced the same bytes each time.
    """
    definitions = [_definition(factory) for factory in factories]
    signatures = [definition.function_signature for definition in definitions]
    conflicting = FunctionSignature.get_conflicting_type_names(signatures)
    declared = FunctionSignature.render_type_definitions(signatures, conflicting)

    def rendered(body: str) -> list[str]:
        return [
            definition.render_signature(
                body, is_async=True, conflicting_type_names=conflicting
            )
            for definition in definitions
        ]

    catalog = "".join(
        fenced_code_block("\n\n".join(block), ".python")
        for block in (declared, rendered(_CATALOG_BODY))
        if block
    )

    return catalog, "\n\n".join([_STUB_HEADER, *declared, *rendered(_STUB_BODY)])


@cache
def _keyword_only(factory: AsyncToolFactory[Any]) -> inspect.Signature:
    """The signature every host function built from *factory* is stamped with."""
    params = ToolSpec.from_factory(factory).params

    return inspect.Signature(
        [param.replace(kind=param.KEYWORD_ONLY) for param in params],
        return_annotation=Any,
    )


def _host_function(factory: AsyncToolFactory[Any], tool: AsyncTool[Any]) -> _HostFunction:
    """Wrap a tool as the coroutine the sandbox calls and awaits.

    Keyword-only, which is what the rendered signatures declare and what the
    harness's sandboxed tools are: an argument list a program spells out is
    also the one a reader of that program can follow.  The call metadata is
    stamped through :meth:`ToolSpec.apply_to`, as both sibling adapters do, so
    the object says what it accepts rather than leaving the docstring to.
    What a failure becomes inside the program is :class:`HostCalls`' to decide.
    """
    spec = ToolSpec.from_factory(factory)

    async def call(**kwargs: Any) -> JsonValue:
        result = await tool(**spec.validate_arguments(kwargs))

        return spec.serialize_data(result.data)

    spec.apply_to(call, _keyword_only(factory), {**spec.annotations, "return": Any})

    return call


def monty_declarations(factories: Sequence[AsyncToolFactory[Any]]) -> str:
    """What the model is shown for *factories*, rendered without building one.

    All the prompt needs, and asking for it never runs a factory: the rendering
    is a pure function of the tools, while a built tool is bound to one run's
    dependencies.
    """
    if not factories:
        return ""

    declarations, _ = _rendered(tuple(factories))

    return declarations


def monty_surface(
    tools: Mapping[AsyncToolFactory[Any], AsyncTool[Any]],
) -> MontySurface:
    """Build the host functions and both renderings for *tools*.

    Each tool comes built, keyed by the factory that describes it, so the
    caller decides what a factory is handed: the run's deps for most, the run
    itself for one that spends its usage.  A tool is built once for the whole
    program rather than per call, since a program may call one many times and
    the fields a factory wires up do not change between them.
    """
    if not tools:
        return MontySurface()

    declarations, stubs = _rendered(tuple(tools))

    return MontySurface(
        functions={
            ToolSpec.from_factory(factory).name: _host_function(factory, tool)
            for factory, tool in tools.items()
        },
        declarations=declarations,
        stubs=stubs,
    )
