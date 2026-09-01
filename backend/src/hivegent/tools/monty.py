"""Adapter exposing tool classes as host functions inside the Monty sandbox.

The mount already makes ``open`` and ``iterdir`` the read tools, ``re`` grep,
and ``json`` jq, which leaves exactly the tools whose answer no program can
compute for itself: retrieval needs the database, the web tools need the
network, and a spreadsheet needs a decoder Monty does not have.  Those are
injected here, so one program searches, queries, and counts in a single call
rather than spending a turn and a `.scratch/` file per step.  Nothing that
mutates is injected and nothing needs to be: a program cannot stop to ask for
approval, and every function here is a read.

What crosses the boundary is the structured ``data`` channel, as the plain
objects ``to_jsonable_python`` makes of it — the same pydantic-core serialiser
a ``.json`` ``output_path`` writes with, stopping at objects rather than going
on to bytes a program would only parse back.  The model-facing ``text`` channel
stays behind, as its budgets, truncation, and hints exist to fit a context
window a program does not have, and a program that wanted fewer rows can say so
in the query.

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
"""

import inspect
import types
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache, reduce
from operator import or_
from typing import Any, get_args

from pydantic import TypeAdapter, create_model
from pydantic_ai.function_signature import FunctionSignature
from pydantic_ai.tools import ToolDefinition
from pydantic_core import to_jsonable_python

from ..converters.base import fenced_code_block
from .base import AsyncTool, CallInfo, ToolOutput, translate_tool_retry
from .sink import OutputPathArg, RedirectedOutput

__all__ = ["MontySurface", "monty_surface"]

type _Factory[D] = Callable[[D], AsyncTool[Any]]
"""What the surface takes: a factory whose tool a program can await.

``AsyncTool`` and not ``Tool``, because the sandbox calls a host function with
``await``: a synchronous tool would be handed over as a coroutine that never
was one, and the mismatch belongs in the signature rather than in a runtime
check inside the wrapper.
"""

type _HostFunction = Callable[..., Awaitable[Any]]
"""What one injected tool becomes, whose payload the rendered stub declares."""

_STUB_HEADER = "import asyncio\nfrom typing import Any, Literal, NotRequired, TypedDict"
"""What the harness puts at the head of its stubs, and for the same reasons."""

_CATALOG_BODY = "..."
"""What a declaration's body is in the catalog the model reads."""

_STUB_BODY = "raise NotImplementedError()"
"""What it is in the stubs the checker reads, as the harness spells it."""


@dataclass(slots=True, frozen=True)
class MontySurface:
    """The host functions a program may call, and the stub declaring them."""

    external_lookup: Mapping[str, _HostFunction] = types.MappingProxyType({})
    declarations: str = ""
    """What the model is shown: the injected tools and the records they return.

    Empty when nothing is injected, where the prompt says nothing rather than
    opening an empty block.
    """

    stubs: str = ""
    """What the type checker is given, which the mount's own stub joins.

    It differs from :attr:`declarations` by what the checker needs and the model
    does not — the ``typing`` imports — and by the body of each declaration,
    which neither of them reads.
    """

    def __bool__(self) -> bool:
        """Whether anything was injected; a dataclass is otherwise always truthy."""
        return bool(self.declarations)


def _result_type(info: CallInfo) -> Any:
    """The payload a tool's ``ToolOutput[...]`` return annotation carries.

    ``RedirectedOutput`` is dropped rather than described: the redirect argument
    is not on the built signature, so the receipt branch it names is
    unreachable from a program and declaring it would only invite a check for a
    variant that never arrives.

    Read off pydantic's generic metadata rather than :func:`get_args`, since
    ``ToolOutput`` is a model: parameterising one builds a real subclass, and
    the typing introspection that works on every other annotation reports no
    arguments at all for it.  ``CallInfo`` has already bound the tool class's
    own type parameters, so what comes out is concrete.

    The receipt is looked for among the payload's arguments rather than behind
    an origin test, which asks the question directly and leaves nothing to keep
    in step with how a union is spelled.
    """
    (payload,) = info.returns.__pydantic_generic_metadata__["args"]
    members = get_args(payload)

    if RedirectedOutput not in members:
        return payload

    return reduce(or_, (member for member in members if member is not RedirectedOutput))


def _parameters_schema(info: CallInfo) -> dict[str, Any]:
    """The JSON schema of a tool's arguments, defaults and descriptions included.

    Built through pydantic rather than read off the registered pydantic-ai tool:
    a tool is built per run here and may be injected on a surface that never
    registers it at all, and the ``Annotated`` metadata each argument already
    carries is the same thing either route would read.
    """
    fields: dict[str, Any] = {
        param.name: (
            info.annotations[param.name],
            ... if param.default is param.empty else param.default,
        )
        for param in info.params
    }

    return create_model(f"{info.name}_arguments", **fields).model_json_schema()


def _sandbox_info(factory: _Factory[Any]) -> CallInfo:
    """One tool's call metadata as the sandbox takes it.

    The redirect is dropped here and only here, so the signature a program is
    declared and the signature it is given cannot come apart: a program already
    holds the value, so a copy in the workspace is a write it did not need and
    could not have had approved.
    """
    return CallInfo.from_factory(factory).without(OutputPathArg)


def _definition(factory: _Factory[Any]) -> ToolDefinition:
    """Describe one tool the way the renderer wants it.

    A :class:`ToolDefinition` rather than a bare ``FunctionSignature``, since it
    is what pairs the name and description with the two schemas and caches the
    signature built from them, which is one invariant this module then does not
    have to keep by hand.
    """
    info = _sandbox_info(factory)

    return ToolDefinition(
        name=info.name,
        parameters_json_schema=_parameters_schema(info),
        description=info.description,
        return_schema=TypeAdapter(_result_type(info)).json_schema(
            mode="serialization"
        ),
    )


@cache
def _rendered(factories: tuple[_Factory[Any], ...]) -> tuple[str, str]:
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


def _host_function(info: CallInfo, tool: AsyncTool[Any]) -> _HostFunction:
    """Wrap a tool as the coroutine the sandbox calls and awaits.

    Keyword-only, which is what the rendered signatures declare and what the
    harness's sandboxed tools are: an argument list a program spells out is
    also the one a reader of that program can follow.  The call metadata is
    stamped through :meth:`CallInfo.apply_to`, as both sibling adapters do, so
    the object says what it accepts rather than leaving the docstring to.

    A :class:`ToolRetry` becomes a ``ValueError`` so it crosses as an ordinary
    exception the program may catch and the traceback names, which is the
    nearest thing a running program has to the correction a tool call gets.
    """

    async def call(**kwargs: Any) -> Any:
        with translate_tool_retry(ValueError):
            result: ToolOutput[Any] = await tool(**kwargs)

        return to_jsonable_python(result.data)

    keyword_only = [
        param.replace(kind=param.KEYWORD_ONLY) for param in info.params
    ]
    info.apply_to(
        call,
        inspect.Signature(keyword_only, return_annotation=Any),
        {**info.annotations, "return": Any},
    )

    return call


def monty_surface[D](factories: Sequence[_Factory[D]], deps: D) -> MontySurface:
    """Build the host functions and both renderings for the tools *factories* name.

    Each tool is built once for the whole run rather than per call, since a
    program may call one many times and the fields a factory wires up do not
    change between them.
    """
    if not factories:
        return MontySurface()

    declarations, stubs = _rendered(tuple(factories))
    lookup: dict[str, _HostFunction] = {}

    for factory in factories:
        info = _sandbox_info(factory)
        lookup[info.name] = _host_function(info, factory(deps))

    return MontySurface(external_lookup=lookup, declarations=declarations, stubs=stubs)
