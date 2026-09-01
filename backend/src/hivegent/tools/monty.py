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
objects the tool's declared result type serialises to.  That declared type is
also what the rendered stub names and what ``return_schema`` publishes, so what
a program receives and what it was told to expect are one description read
twice, and it stops at objects rather than going on to the bytes a ``.json``
``output_path`` writes and a program would only parse back.  The model-facing ``text`` channel
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
from functools import cache
from typing import Any

from pydantic import JsonValue
from pydantic_ai.function_signature import FunctionSignature
from pydantic_ai.tools import ToolDefinition

from ..converters.base import fenced_code_block
from .base import AsyncTool, ToolSpec, translate_tool_retry
from .sink import OutputPathArg

__all__ = ["MontySurface", "monty_surface"]

type _Factory[D] = Callable[[D], AsyncTool[Any]]
"""What the surface takes: a factory whose tool a program can await.

``AsyncTool`` and not ``Tool``, because the sandbox calls a host function with
``await``: a synchronous tool would be handed over as a coroutine that never
was one, and the mismatch belongs in the signature rather than in a runtime
check inside the wrapper.
"""

type _HostFunction = Callable[..., Awaitable[JsonValue]]
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


@cache
def _sandbox_spec(factory: _Factory[Any]) -> ToolSpec:
    """One tool's call metadata as the sandbox takes it.

    The redirect is dropped, so the signature a program is declared and the
    signature it is given cannot come apart: a program already holds the value,
    so a copy in the workspace is a write it did not need and could not have
    had approved.  Dropping the argument drops the receipt branch it names with
    it, which is why the rendered stub declares only the payload: the variant
    is unreachable from a program, and declaring it would invite a check for
    something that never arrives.
    """
    return ToolSpec.from_factory(factory).without(OutputPathArg)


def _definition(factory: _Factory[Any]) -> ToolDefinition:
    """Describe one tool the way the renderer wants it.

    A :class:`ToolDefinition` rather than a bare ``FunctionSignature``, since it
    is what pairs the name and description with the two schemas and caches the
    signature built from them, which is one invariant this module then does not
    have to keep by hand.
    """
    spec = _sandbox_spec(factory)

    return ToolDefinition(
        name=spec.name,
        parameters_json_schema=spec.parameters_json_schema,
        description=spec.description,
        return_schema=spec.data_json_schema,
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


def _host_function(spec: ToolSpec, tool: AsyncTool[Any]) -> _HostFunction:
    """Wrap a tool as the coroutine the sandbox calls and awaits.

    Keyword-only, which is what the rendered signatures declare and what the
    harness's sandboxed tools are: an argument list a program spells out is
    also the one a reader of that program can follow.  The call metadata is
    stamped through :meth:`ToolSpec.apply_to`, as both sibling adapters do, so
    the object says what it accepts rather than leaving the docstring to.

    A :class:`ToolRetry` becomes a ``ValueError`` so it crosses as an ordinary
    exception the program may catch and the traceback names, which is the
    nearest thing a running program has to the correction a tool call gets.
    """

    async def call(**kwargs: Any) -> JsonValue:
        with translate_tool_retry(ValueError):
            result = await tool(**spec.validate_arguments(kwargs))

        return spec.serialize_data(result.data)

    keyword_only = [param.replace(kind=param.KEYWORD_ONLY) for param in spec.params]
    spec.apply_to(
        call,
        inspect.Signature(keyword_only, return_annotation=Any),
        {**spec.annotations, "return": Any},
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
        spec = _sandbox_spec(factory)
        lookup[spec.name] = _host_function(spec, factory(deps))

    return MontySurface(external_lookup=lookup, declarations=declarations, stubs=stubs)
