"""Run small Python programs in the Monty sandbox."""

import ast
import asyncio
import keyword
import reprlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Annotated

from exar.text import hint_suffix, plural
from exar.tools import Retry, effect
from exar.tools.batch import tool_return
from exar.tools.common import MAX_DOCUMENT_CHARS, read_text_or_retry
from pydantic import Field, JsonValue
from pydantic_ai import ToolReturn
from pydantic_monty import (
    AsyncMonty,
    CollectString,
    MontyDisconnectError,
    MontyError,
    MontyRuntimeError,
    MontyShutdown,
    MontySyntaxError,
    MontyTypingError,
    ResourceLimits,
)

from .base import CallBudget, SearchPath, ToolFunc, resolve_file_or_retry, sidecar_hint
from .changeset import (
    ChangesetOutcome,
    CommitChanges,
    changes_report,
    stage_changes,
)
from .monty import HostCall, HostCalls, MontySurface
from .workspace_os import MOUNT_STUB, WORKSPACE_MOUNT, ChangesetLimits, WorkspaceOS

__all__ = [
    "CodeArg",
    "PythonArgsArg",
    "PythonResult",
    "PythonScriptPathArg",
    "ScriptCheck",
    "check_script",
    "is_python_script",
    "run_python",
    "validate_program_source",
]


def is_python_script(file_path: str) -> bool:
    """Return whether *file_path* names a program this tool will run.

    The one table behind the two halves of the stored-program flow, for the
    same reason :func:`~hivegent.shared.formats.is_tabular` is one behind the
    read/query split: the write that points a file at ``script_path`` and the
    run that accepts it have to agree on which files those are, or a receipt
    promises a rerun the tool then refuses.

    >>> is_python_script("/tmp/run.PY")
    True
    >>> is_python_script("~/notes.md")
    False
    """
    return PurePosixPath(file_path).suffix.lower() == ".py"


_CUT = "\x00"
"""Where :data:`_VALUE_REPR` left something out, which no ``repr`` of a value spells raw."""

_VALUE_REPR = reprlib.Repr(
    fillvalue=_CUT,
    maxlevel=6,
    maxtuple=1000,
    maxlist=1000,
    maxarray=1000,
    maxdict=1000,
    maxset=1000,
    maxfrozenset=1000,
    maxdeque=1000,
    maxstring=100_000,
    maxlong=100_000,
    maxother=100_000,
)
"""Elides a large result while rendering an ordinary one exactly as ``repr``.

The sandbox converts the trailing expression into a real Python object, so a
program that ends in a large collection would spend megabytes and hundreds of
milliseconds building a string far past what a tool return shows.  The caps sit
well above that, so an ordinary value is rendered whole and only the runaway
case is cut, before the string is built rather than after.  Every cut is marked
by :data:`_CUT`, shown as ``...`` once the output has named it.
"""

_ELIDED_HINT = (
    "the result is cut where it shows '...', print() it or write it to a file "
    "to get all of it"
)

CodeArg = Annotated[
    str | None,
    Field(
        description=(
            "The program itself, written inline, for a throwaway. Provide "
            "either this or `script_path`, never both, and neither names the "
            "data: a program opens the documents it reads by their workspace "
            "path. Anything past a few lines belongs in a `/tmp` `.py` file run "
            "by `script_path`, where a runtime error costs one edit_document "
            "instead of a retyped program."
        ),
    ),
]
PythonScriptPathArg = Annotated[
    str | None,
    Field(
        description=(
            "Full path of a stored `.py` program to run instead of "
            "inline `code`. The file named here is the program, never a "
            "document it reads. It is loaded fresh on every call, so it can be "
            "repaired with `edit_document` and run again."
        ),
    ),
]
PythonArgsArg = Annotated[
    dict[str, JsonValue] | None,
    Field(
        description=(
            "Keyword arguments to call the `main` function the stored script "
            "at `script_path` defines, whose return value is then the result. "
            "Only with `script_path`, and `{}` calls `main()` with none."
        ),
    ),
]
_PROGRAM_SOURCES = (
    "`code`, the program written inline, or `script_path`, a stored `.py` "
    "program to run. Neither names a document the program reads: it opens "
    "those itself."
)
"""The two ways a program arrives, named once for the two refusals about them."""


def _given(argument: str | None) -> str | None:
    """Read a blank argument as the absence a model meant it for.

    >>> _given("  ") is None
    True
    >>> _given("x = 1")
    'x = 1'
    """
    return argument if argument and argument.strip() else None


def validate_program_source(
    code: str | None,
    script_path: str | None,
    args: Mapping[str, JsonValue] | None = None,
) -> tuple[str | None, str | None]:
    """Require one nonblank program source and return normalized arguments.

    *args* call a stored script's ``main``, so they need a ``script_path``
    and keyword names.
    """
    code, script_path = _given(code), _given(script_path)

    if args is not None and script_path is None:
        raise Retry(
            "`args` call the `main` of a stored script, so they need "
            "`script_path`. Inline `code` states its values itself."
        )

    if bad := [key for key in args or () if not _keyword(key)]:
        raise Retry(f"`args` keys must be Python keyword argument names, not {bad}.")

    if code is not None and script_path is not None:
        raise Retry(
            f"`code` and `script_path` were both given, and a call runs one "
            f"program. Drop `code` to run the stored '{script_path}', or "
            "drop `script_path` to run the inline program."
        )

    if code is None and script_path is None:
        raise Retry(f"The program is empty. Provide {_PROGRAM_SOURCES}")

    return code, script_path


def _keyword(name: str) -> bool:
    """Whether *name* can be passed as a keyword argument.

    >>> _keyword("table"), _keyword("class"), _keyword("a-b")
    (True, False, False)
    """
    return name.isidentifier() and not keyword.iskeyword(name)


@dataclass(slots=True, frozen=True)
class ScriptCheck:
    """Whether a stored script runs by the ``main`` convention, and how it is called.

    Attributes:
        signature: Its ``main`` as defined, such as ``main(table: str) -> int``.
        summary: The first line of its docstring, or the module's.
        is_async: Whether ``main`` is a coroutine, so its call is awaited.
        problem: Why it does not run, ``None`` when it does.
    """

    signature: str | None = None
    summary: str | None = None
    is_async: bool = False
    problem: str | None = None

    def call(self, args: Mapping[str, JsonValue]) -> str:
        """The call of ``main`` with *args*, spelled as literals for the type checker.

        >>> ScriptCheck("main(n: int)").call({"n": 2, "tags": ["a"]})
        "main(n=2, tags=['a'])"
        """
        call = f"main({', '.join(f'{key}={value!r}' for key, value in args.items())})"

        return f"await {call}" if self.is_async else call


def _script_main(source: str, script_name: str) -> ScriptCheck:
    """Find the top-level ``main`` *source* defines, parsed and never run.

    >>> _script_main("def main(n: int) -> int:\\n    return n", "s.py").signature
    'main(n: int) -> int'
    >>> _script_main("x = 1", "s.py").problem
    'It defines no top-level main function to call.'
    """
    try:
        tree = ast.parse(source, script_name)
    except SyntaxError as exc:
        return ScriptCheck(problem=f"It is no valid Python: {exc}")

    main = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == "main"
        ),
        None,
    )

    if main is None:
        return ScriptCheck(problem="It defines no top-level main function to call.")

    is_async = isinstance(main, ast.AsyncFunctionDef)
    returns = "" if main.returns is None else f" -> {ast.unparse(main.returns)}"
    doc = ast.get_docstring(main) or ast.get_docstring(tree)

    return ScriptCheck(
        signature=f"{'async ' if is_async else ''}main({ast.unparse(main.args)}){returns}",
        summary=doc.strip().splitlines()[0] if doc else None,
        is_async=is_async,
    )


def _default_limits() -> ResourceLimits:
    """The budget one program runs under when no caller sets one."""
    return {
        "max_feed_duration_secs": 5.0,
        "max_total_sleep_secs": 5.0,
        "max_memory": 256_000_000,
    }


_MAX_LISTED_CALLS = 20
"""How many finished host calls a failure lists, each already bounded."""


def _finished_calls(calls: Sequence[HostCall]) -> str:
    """List the host calls that finished before a failure, or nothing when none did.

    >>> print(_finished_calls([HostCall("search", "query='x'", result="[]")]))
    1 host call finished before the failure, and a rerun pays for it again:
    - search(query='x') -> []
    """
    if not calls:
        return ""

    count = len(calls)
    lines = [f"- {call.render()}" for call in calls[:_MAX_LISTED_CALLS]]

    if count > _MAX_LISTED_CALLS:
        lines.append(f"- and {count - _MAX_LISTED_CALLS} more")

    header = (
        f"{count} host {plural(count, 'call')} finished before the failure, "
        f"and a rerun pays for {plural(count, 'it', 'each')} again:"
    )

    return "\n".join([header, *lines])


def _diagnostic(exc: MontyError, printed: str, calls: Sequence[HostCall]) -> str:
    """Render a sandbox failure as text the model can repair its program from.

    A traceback is the whole correction for a program the model wrote itself,
    so the three error classes that carry one render it in full. Output printed
    before the failure leads, since a program that reports its own progress
    says more about where it went wrong than the frame the interpreter stopped
    in.  The host calls that finished follow, so a rerun reuses what they
    returned rather than calling them again.

    A typing error is the cheapest of the three, since it arrives before the
    program ran at all: nothing was read, nothing was staged, and the
    diagnostic names the field or argument the injected stub disagrees with.
    """
    detail = (
        exc.display()
        if isinstance(exc, MontySyntaxError | MontyRuntimeError | MontyTypingError)
        else str(exc)
    )
    printed = printed.rstrip("\n")
    parts = (
        printed and f"Printed before the failure:\n{printed}",
        _finished_calls(calls),
        detail,
    )

    return "\n\n".join(filter(None, parts))


@dataclass(slots=True, frozen=True)
class PythonResult:
    """What one program produced: its value and anything it printed."""

    result: str | None = None
    """The trailing expression's value, or ``None`` when there was none."""

    stdout: str = ""
    script_path: str | None = None
    """Canonical workspace path when the program came from a stored script."""

    changeset: ChangesetOutcome | None = None
    """What the program changed, applied or awaiting ``apply_changes``, ``None`` when only ``/tmp`` changed at once."""

    calls: tuple[HostCall, ...] = ()
    """The host calls that finished, in the order they did, which the card lists."""


_EMPTY_SURFACE = MontySurface()
"""The default surface, safe to share since nothing mutates one."""


def _no_script_environ(_path: str) -> Mapping[str, str]:
    """Add no variables for a stored script."""
    return {}


def _output(
    value: object,
    printed: str,
    *,
    script_path: str | None,
    changeset: ChangesetOutcome | None,
    calls: tuple[HostCall, ...],
) -> ToolReturn[PythonResult]:
    """Render what the program produced for the model, whole.

    The text is bounded where every tool return is, so the data keeps all
    of it and a saved copy of the text is the whole of it too.
    """
    stdout = printed.rstrip("\n")
    rendered = None if value is None else _VALUE_REPR.repr(value)
    elided = rendered is not None and _CUT in rendered
    result = rendered and rendered.replace(_CUT, "...")
    body = "\n".join(filter(None, (stdout, result and f"Result: {result}")))
    body += hint_suffix([_ELIDED_HINT] if elided else [])

    return tool_return(
        PythonResult(
            result=result,
            stdout=stdout,
            script_path=script_path,
            changeset=changeset,
            calls=calls,
        ),
        "\n\n".join(
            filter(
                None,
                (
                    body or "The program printed nothing and returned no value.",
                    changes_report(changeset),
                ),
            )
        ),
    )


def _prepare(
    paths: tuple[SearchPath, ...],
    max_document_chars: int,
    code: str | None,
    script_path: str | None,
    args: Mapping[str, JsonValue] | None,
) -> tuple[str, str | None]:
    """Resolve the program to run and the canonical script it came from, off the event loop.

    Inline ``code`` answers to the same cap a stored script's read does,
    which no read ever sizes.
    """
    code, script_path = validate_program_source(code, script_path, args)
    source, canonical_script = code or "", None

    if script_path is not None:
        sp, local, _absolute = resolve_file_or_retry(paths, script_path)
        canonical_script = sp.mount.render(local)

        if not is_python_script(canonical_script):
            raise Retry(
                f"'{canonical_script}' is not a `.py` script. `script_path` "
                "names the program to run, not a document it reads."
            )

        source = read_text_or_retry(
            sp.mount,
            local,
            canonical_script,
            max_document_chars,
            sidecar_hint(canonical_script),
        ).text

    if len(source) > max_document_chars:
        raise Retry(
            f"The program is too large for one Python run ({len(source)} "
            f"characters, maximum {max_document_chars})."
        )

    return source, canonical_script


def _mount(
    paths: tuple[SearchPath, ...],
    environ: Mapping[str, str],
    *,
    limits: ChangesetLimits,
    writable: tuple[SearchPath, ...],
    max_document_chars: int,
) -> WorkspaceOS:
    """Build the filesystem: the roots mounted, the environment beside them.

    The environment mirrors an ordinary shell's, so ``PWD`` agrees with
    ``os.getcwd()``.
    """
    return WorkspaceOS(
        paths=paths,
        environ={
            "PWD": str(WORKSPACE_MOUNT),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            **environ,
        },
        limits=limits,
        writable=writable,
        max_document_chars=max_document_chars,
    )


def run_python(
    paths: tuple[SearchPath, ...] = (),
    *,
    pool: Callable[[], AsyncMonty],
    changeset_limits: ChangesetLimits,
    writable: tuple[SearchPath, ...] = (),
    commit: CommitChanges | None = None,
    environ: Mapping[str, str] | None = None,
    surface: MontySurface = _EMPTY_SURFACE,
    type_check: bool = True,
    script_environ: Callable[[str], Mapping[str, str]] = _no_script_environ,
    max_host_calls: int = 100,
    max_wall_seconds: float | None = None,
    limits: ResourceLimits | None = None,
    max_document_chars: int = MAX_DOCUMENT_CHARS,
) -> ToolFunc:
    """The tool that runs a Python program in a sandbox that reaches nothing outside itself.

    Each call takes a fresh session out of the pool, so one program never sees
    another's variables, and a session a time limit stopped mid-operation,
    which Monty leaves with no guarantees about its heap, is never fed again.

    Args:
        paths: The roots a program reads.
        pool: The sandbox pool, fetched on each call since the factory may run
            outside the server lifespan.
        changeset_limits: Bounds of what one program may change.
        writable: The roots a program may change, narrower than the ones it reads.
            Only the direct ones (``/tmp``) in a mode that may not write the
            workspace, which is what makes every change to it from inside a
            program refuse exactly where ``write_document`` does.
        commit: Applies what a program changed, or stages all of it when any
            change asks for approval.
        environ: Variables beside the ones every run has, such as ``HOME`` and
            ``USER``, decided where the tool is built, since who runs it and
            which workspace is theirs is a property of the run.
        surface: The tools a program may call, and the stub that declares them.
            Empty by default, which is the sandbox with the mount and nothing else.
        type_check: Whether a program is type-checked against *surface* before
            it runs.  A misread result shape costs a diagnostic that names the
            field rather than a run that reads documents and then fails, and a
            forgotten ``await`` on an injected tool is reported as such.  It is
            a whole type checker, so it also rejects unsound code that would have
            run, which is why the surface declares the mount's ``open`` too.
        script_environ: Variables a stored script adds to *environ*, from its
            canonical path, derived by the host from where the script lies,
            never from what the model passed.
        max_host_calls: How many calls one program may make to the functions
            of *surface*.
        max_wall_seconds: How long one program may wait on the functions of
            *surface*, see :class:`HostCalls`.
        limits: The sandbox's resource limits, a default budget when omitted.
        max_document_chars: Cap on any one document this run reads or writes.
            Decoding happens in the server process, before the text is handed
            to the sandbox, so the memory budget of *limits* does not cover it.
            The mount applies the same cap to every document the program opens.
    """
    limits = _default_limits() if limits is None else limits
    base_environ = {} if environ is None else environ
    # The mount's half stands whether or not a tool was injected, since
    # without it every program that opens a document is rejected before it runs.
    stubs = "\n\n".join(filter(None, (MOUNT_STUB, surface.stubs)))

    @effect("write")
    async def run_python(
        code: CodeArg = None,
        script_path: PythonScriptPathArg = None,
        args: PythonArgsArg = None,
    ) -> ToolReturn[PythonResult]:
        """Run a short program in the Monty interpreter.

        Provide exactly one of `code` or `script_path` on every call.
        A stored script defining `main`, such as a skill's, is called with
        `args` and returns its result.

        Reach for it when an answer turns on arithmetic, dates, sorting, or
        counting, and when one spans more documents than it could quote from:
        one program reads them all and returns the little the answer needs.

        Monty runs a subset of Python and its standard library rather than a
        CPython environment, so an import it lacks says so by name and is
        worth trying rather than working around, and the program reaches
        nothing outside itself but the workspace, this conversation's `/tmp`,
        and the functions declared to it: no network of its own, and no host
        filesystem.

        The declared functions are the tools themselves, awaited inside the
        program, so `res = await query_table(file_paths=[...], queries=[...])` reads a
        spreadsheet the interpreter cannot decode and hands the program every
        row of the result. Reach for one directly rather than staging its
        output through a file: a tool called here needs no read back and no
        guessing at the shape of what was written.

        End the program with the expression whose value you want back, and
        print anything else worth seeing.

        The workspace behaves like a normal filesystem: a program may write,
        append, rename, and remove files and directories with the ordinary
        `Path` and `open` calls.  Nothing changes while it runs, its reads
        see its own changes, and once it succeeds its changes land as one
        changeset: at once when only `/tmp` changed, and otherwise staged,
        `/tmp` included, for `apply_changes` to apply after the user approves it.
        """
        source, canonical_script = await asyncio.to_thread(
            _prepare, paths, max_document_chars, code, script_path, args
        )
        call = None

        if args is not None and canonical_script is not None:
            main = _script_main(source, canonical_script)

            if main.problem is not None:
                raise Retry(
                    f"'{canonical_script}' cannot be called with `args`. "
                    f"{main.problem} Run it without `args` instead."
                )

            call = main.call(args)

        filesystem = _mount(
            paths,
            {
                **base_environ,
                **(
                    {} if canonical_script is None else script_environ(canonical_script)
                ),
            },
            limits=changeset_limits,
            writable=writable,
            max_document_chars=max_document_chars,
        )
        printed = CollectString()
        calls = HostCalls(CallBudget(max_host_calls), max_wall_seconds=max_wall_seconds)

        async with pool().checkout(
            script_name=canonical_script or "script.py",
            limits=limits,
            type_check=type_check,
            type_check_stubs=stubs,
            type_check_format="concise",
        ) as session:
            try:
                external_lookup = calls.bind(surface)
                value = await session.feed_run(
                    source,
                    print_callback=printed,
                    cwd=str(WORKSPACE_MOUNT),
                    os=filesystem,
                    external_lookup=external_lookup,
                )

                # Checked against the definitions the source fed, so a wrong
                # argument is named before `main` runs.
                if call is not None:
                    value = await session.feed_run(
                        call,
                        print_callback=printed,
                        os=filesystem,
                        external_lookup=external_lookup,
                    )

            # The pool itself is gone, which no rewrite of the program fixes.
            except MontyShutdown, MontyDisconnectError:
                raise

            except MontyError as exc:
                calls.raise_ending()

                raise Retry(_diagnostic(exc, printed.output, calls.finished)) from exc

        calls.raise_ending()

        # Only once the program succeeded, so one that fails changes nothing.
        changes = await asyncio.to_thread(stage_changes, filesystem)

        if changes is not None and commit is None:
            raise Retry("This run keeps none of the changes a program makes.")

        changeset = None if changes is None or commit is None else await commit(changes)

        return _output(
            value,
            printed.output,
            script_path=canonical_script,
            changeset=changeset,
            calls=tuple(calls.finished),
        )

    return run_python


async def check_script(
    pool: AsyncMonty,
    source: str,
    *,
    script_name: str,
    stubs: str,
    environ: Mapping[str, str],
    limits: ResourceLimits,
) -> ScriptCheck:
    """Whether *source* runs by the ``main`` convention, type-checked against *stubs*.

    Its definitions are fed to a session with a filesystem holding nothing and
    no host functions, which is how the checker sees them: Monty checks only
    what it is fed.  What runs at the top level reaches nothing, and is
    bounded by *limits*.
    """
    check = _script_main(source, script_name)

    if check.problem is not None:
        return check

    filesystem = WorkspaceOS(
        paths=(),
        environ=dict(environ),
        limits=ChangesetLimits(max_operations=0, max_deletes=0, max_chars=0),
    )

    async with pool.checkout(
        script_name=script_name,
        limits=limits,
        type_check=True,
        type_check_stubs=stubs,
        type_check_format="concise",
    ) as session:
        try:
            _ = await session.feed_run(
                source,
                print_callback=CollectString(),
                cwd=str(WORKSPACE_MOUNT),
                os=filesystem,
            )

        except MontyShutdown, MontyDisconnectError:
            raise

        except MontyError as exc:
            return replace(check, problem=_diagnostic(exc, "", ()))

    return check
