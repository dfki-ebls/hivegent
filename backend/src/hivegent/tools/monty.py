"""Adapter exposing plain tools as host functions inside the Monty sandbox.

The mount already makes ``open`` and ``iterdir`` the read tools, ``re`` grep,
and ``json`` jq, which leaves exactly the tools whose answer no program can
compute for itself: retrieval needs the database, the web tools need the
network, and a spreadsheet needs a decoder Monty does not have.  Those are
injected here, so one program searches, queries, and counts in a single call
rather than spending a turn and a ``/tmp`` file per step.  Nothing that
mutates is injected and nothing needs to be: a program cannot stop to ask for
approval, and every function here is a read.

What crosses the boundary is the typed data a tool returns as its
``ToolReturn.metadata``, as the plain objects it serialises to.  Its type, the
parameter of the tool's ``ToolReturn[T]`` annotation, is also what pydantic-ai
publishes as ``return_schema`` and what the rendered stub names, so what a
program receives and what it was told to expect are one description read
twice.  The model-facing text stays behind, as its budgets, truncation, and
hints exist to fit a context window a program does not have, and a program that
wanted fewer rows can say so in the query.

pydantic-ai-harness's ``CodeMode`` was evaluated and not adopted.
A program needs the typed tool metadata rather than model-facing text, the
``WorkspaceOS`` with its staged writes, and no approval in the middle of a run.

The declarations are rendered by pydantic-ai's own
:class:`~pydantic_ai.function_signature.FunctionSignature`, which is what
``pydantic-ai-harness``'s code mode uses for the same purpose, so this module
supplies the two JSON schemas per tool and does none of the type rendering
itself.  That is worth more than it looks: the renderer states a defaulted
field as ``NotRequired``, carries each field's own description into the stub,
and disambiguates two tools whose result records share a class name by
prefixing each with its function's name (the last of which a name-keyed
renderer gets silently and catastrophically wrong), declaring the second tool
with the first one's fields so that reading its result correctly is the thing
the type check rejects.

The result is produced twice, exactly as the harness does it:
:attr:`MontySurface.declarations` for the model and :attr:`MontySurface.stubs`
for Monty's ``type_check_stubs``, so the shape promised and the shape enforced
are one text.  Rendering depends on the tool definitions alone, so it is
cached on them, while the host functions are rebuilt per run because they close
over the run's deps.

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

import asyncio
import inspect
import reprlib
import types
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import InitVar, dataclass, field, replace
from functools import cache, wraps
from typing import Any

from exar.helpers import get_name
from exar.text import fenced, truncate_line
from exar.tools import Retry
from exar.tools.batch import unpack
from pydantic import JsonValue, ValidationError
from pydantic_ai import Tool
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.function_signature import FunctionSignature
from pydantic_ai.tools import ToolDefinition
from pydantic_core import SchemaValidator, to_jsonable_python

from .base import CallBudget, ToolFunc, render_arguments

__all__ = [
    "HostCall",
    "HostCalls",
    "MontySurface",
    "monty_surface",
]

_TURN_ENDING_ERRORS: tuple[type[Exception], ...] = (UsageLimitExceeded,)
"""What a host function may raise that ends the turn rather than the program.

The turn's shared usage limit is spent for every run in it, so no rewrite of
the program could get past it.
"""

type _HostFunction = Callable[..., Awaitable[JsonValue]]
"""What one injected tool becomes, whose payload the rendered stub declares."""


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

    >>> _program_error("complete", Retry("give me a query"))
    ValueError('give me a query')
    >>> _program_error("complete", OSError("/srv/secret"))
    RuntimeError('complete raised OSError')
    """
    if isinstance(exc, Retry | ValidationError):
        return ValueError(str(exc))

    return RuntimeError(f"{name} raised {type(exc).__name__}")


def _out_of_time() -> TimeoutError:
    return TimeoutError(
        "This program reached its time limit, so no further host call runs. "
        "Finish with the results already obtained, or move the rest into a "
        "second program."
    )


@dataclass(slots=True)
class HostCalls:
    """What one program asks of the host: its budget, its errors, and a record.

    One policy for every function :meth:`bind` hands a program.  A call is
    counted against the program's :attr:`budget` before its first await, so
    fanning out with ``asyncio.gather`` cannot exceed it.  A
    `exar.tools.Retry` or an invalid argument reaches the
    program as a ``ValueError`` it may correct, and any other failure as a
    ``RuntimeError`` naming its type alone, since a host error may carry host
    details.  An exception of :data:`_TURN_ENDING_ERRORS` is not the program's
    failure: the first one is kept in :attr:`ending` for the host to raise once
    the program stopped, and every later call is refused, since the turn it
    would spend on is over.

    A call still running *max_wall_seconds* after the program started is cut
    off there, and every later one is refused, each as a ``TimeoutError`` the
    program may catch to finish with what it has, which is what keeps a
    program from being stopped with all its results lost.  What it can do
    after that is bytecode, sleep, and file operations, which Monty's own
    limits bound.
    """

    budget: CallBudget
    max_wall_seconds: InitVar[float | None] = None
    finished: list[HostCall] = field(default_factory=list)
    ending: Exception | None = None
    deadline: float | None = field(default=None, init=False)
    """When, on the event loop's clock, the program's host calls run out of time."""

    def __post_init__(self, max_wall_seconds: float | None) -> None:
        if max_wall_seconds is not None:
            self.deadline = asyncio.get_running_loop().time() + max_wall_seconds

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

            if (
                self.deadline is not None
                and asyncio.get_running_loop().time() >= self.deadline
            ):
                raise _out_of_time()

            self.budget.take(
                f"This program reached its limit of {self.budget.limit} host "
                "function calls. Batch what one call can take, such as several "
                "queries in one query_table, or move the rest into a second program."
            )

            arguments = render_arguments(kwargs, _PREVIEW_CHARS)

            clock = asyncio.timeout_at(self.deadline)

            try:
                async with clock:
                    result = await function(**kwargs)

            except _TURN_ENDING_ERRORS as exc:
                self.ending = self.ending or exc
                raise

            except Exception as exc:
                error = _out_of_time() if clock.expired() else _program_error(name, exc)
                preview = truncate_line(
                    f"{type(error).__name__}: {error}", _PREVIEW_CHARS
                )
                self.finished.append(HostCall(name, arguments, error=preview))
                raise error from exc

            self.finished.append(HostCall(name, arguments, result=_preview(result)))

            return result

        return call


def _undescribed(schema: Mapping[str, Any]) -> dict[str, Any]:
    """*schema* without the parameter descriptions the renderer lists as ``Args``.

    >>> _undescribed({"properties": {"q": {"type": "string", "description": "x"}}})
    {'properties': {'q': {'type': 'string'}}}
    """
    properties: Mapping[str, Mapping[str, Any]] = schema.get("properties", {})

    return {
        **schema,
        "properties": {
            name: {key: value for key, value in field.items() if key != "description"}
            for name, field in properties.items()
        },
    }


@dataclass(frozen=True, slots=True)
class _Key:
    """What the definition of a tool derives from, whichever run built its function."""

    name: str
    doc: str | None
    signature: inspect.Signature

    @staticmethod
    def of(func: ToolFunc) -> _Key:
        return _Key(get_name(func), func.__doc__, inspect.signature(func))


@dataclass(frozen=True, slots=True)
class _Schema:
    """The definition and argument validator of a tool, which keep nothing of the run that built its function."""

    tool_def: ToolDefinition
    validator: SchemaValidator


_SCHEMAS: dict[_Key, _Schema] = {}
"""The schema of every tool by its key, built once for every run's equal function."""


def _schema(key: _Key, func: ToolFunc) -> _Schema:
    """The schema of *func*, whose key is *key*."""
    schema = _SCHEMAS.get(key)

    if schema is None:
        tool = Tool(func)
        schema = _SCHEMAS[key] = _Schema(tool.tool_def, tool.function_schema.validator)

    return schema


def _described(tool_def: ToolDefinition, listed: frozenset[str]) -> ToolDefinition:
    """*tool_def* as the renderer reads it.

    A tool named in *listed* is one the model also holds in its tool list, whose
    description and arguments it already read there, so they are pointed at
    rather than repeated.  The result records stay, since the tool list has no
    return schema.
    """
    name = tool_def.name

    if name in listed:
        return replace(
            tool_def,
            description=f"Your {name} tool, whose description and arguments apply here.",
            parameters_json_schema=_undescribed(tool_def.parameters_json_schema),
            return_schema=tool_def.return_schema or {},
        )

    return replace(tool_def, return_schema=tool_def.return_schema or {})


@cache
def _render(keys: tuple[_Key, ...], listed: frozenset[str]) -> tuple[str, str]:
    """The catalog and the stubs of these tools, whose schemas are built, rendered once per process.

    Cached because rendering is a pure function of the keys and *listed*, while
    it ran on every model request (the catalog is a dynamic instruction) and
    again on every ``run_python`` call.
    """
    tool_defs = [_described(_SCHEMAS[key].tool_def, listed) for key in keys]
    signatures = [tool_def.function_signature for tool_def in tool_defs]
    conflicting = FunctionSignature.get_conflicting_type_names(signatures)
    declared = FunctionSignature.render_type_definitions(signatures, conflicting)

    def rendered(body: str) -> list[str]:
        return [
            tool_def.render_signature(
                body, is_async=True, conflicting_type_names=conflicting
            )
            for tool_def in tool_defs
        ]

    catalog = "".join(
        fenced("\n\n".join(block), "python")
        for block in (declared, rendered("..."))
        if block
    )
    header = "import asyncio\nfrom typing import Any, Literal, NotRequired, TypedDict"

    return catalog, "\n\n".join(
        [header, *declared, *rendered("raise NotImplementedError()")]
    )


def _host_function(func: ToolFunc, validator: SchemaValidator) -> _HostFunction:
    """Wrap a tool as the coroutine the sandbox calls and awaits.

    The arguments are validated against the tool's own schema, and the program
    receives the typed data of the result as plain objects.  What a failure
    becomes inside the program is :class:`HostCalls`' to decide.
    """

    @wraps(func)
    async def call(**kwargs: Any) -> JsonValue:
        result = await func(**validator.validate_python(kwargs))

        return to_jsonable_python(unpack(result).data)

    return call


def monty_surface(funcs: Sequence[ToolFunc], listed: frozenset[str]) -> MontySurface:
    """Build the host functions and both renderings for the tools *funcs* of one run.

    A tool is built once for the whole program rather than per call, since a
    program may call one many times.  *listed* names the tools the model also holds in its tool list, documented there.
    """
    if not funcs:
        return MontySurface()

    keys = tuple(_Key.of(func) for func in funcs)
    functions = {
        key.name: _host_function(func, _schema(key, func).validator)
        for key, func in zip(keys, funcs, strict=True)
    }
    declarations, stubs = _render(keys, listed)

    return MontySurface(
        functions=functions,
        declarations=declarations,
        stubs=stubs,
    )
