"""Run small Python programs in the Monty sandbox."""

import asyncio
import reprlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Annotated, override

from pydantic import Field
from pydantic_monty import (
    AsyncMonty,
    CollectString,
    MontyDisconnectError,
    MontyError,
    MontyRuntimeError,
    MontyShutdown,
    MontySyntaxError,
    MontyTypingError,
    OSAccess,
    ResourceLimits,
)

from ..humanize import pluralize
from .base import (
    AsyncPathTool,
    SearchPath,
    ToolOutput,
    ToolRetry,
    check_read_budget,
    read_text_or_retry,
    resolve_file_or_retry,
    sidecar_hint,
)
from .changeset import ChangesetOutcome, CommitChanges, PendingChanges, stage_changes
from .formatting import cap_lines, hint_suffix, truncate_line, truncate_middle
from .monty import MontySurface
from .workspace_os import MOUNT_STUB, WORKSPACE_MOUNT, ChangesetLimits, WorkspaceOS

__all__ = [
    "CodeArg",
    "PythonResult",
    "PythonScriptPathArg",
    "RunPythonTool",
    "is_python_script",
    "validate_program_source",
]


def is_python_script(file_path: str) -> bool:
    """Return whether *file_path* names a program this tool will run.

    The one table behind the two halves of the stored-program flow, for the
    same reason :func:`~hivegent.converters.is_tabular` is one behind the
    read/query split: the write that points a file at ``script_path`` and the
    run that accepts it have to agree on which files those are, or a receipt
    promises a rerun the tool then refuses.

    >>> is_python_script("/tmp/run.PY")
    True
    >>> is_python_script("~/notes.md")
    False
    """
    return PurePosixPath(file_path).suffix.lower() == ".py"


_VALUE_REPR = reprlib.Repr(
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
milliseconds building a string the output budget then clips away.  The
caps sit well above that budget, so what a clip would have kept is unaffected
and only the runaway case is cut, before the string is built rather than after.
"""

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
    code: str | None, script_path: str | None
) -> tuple[str | None, str | None]:
    """Require one nonblank program source and return normalized arguments."""
    code, script_path = _given(code), _given(script_path)

    if code is not None and script_path is not None:
        raise ToolRetry(
            f"`code` and `script_path` were both given, and a call runs one "
            f"program. Drop `code` to run the stored '{script_path}', or "
            "drop `script_path` to run the inline program."
        )

    if code is None and script_path is None:
        raise ToolRetry(f"The program is empty. Provide {_PROGRAM_SOURCES}")

    return code, script_path


def _default_limits() -> ResourceLimits:
    """The budget one program runs under when no caller sets one."""
    return {
        "max_feed_duration_secs": 5.0,
        "max_total_sleep_secs": 5.0,
        "max_memory": 256_000_000,
    }


def _budget_lines(text: str, max_chars: int) -> tuple[str, int, bool]:
    """Cap text by line and total size, returning its truncation state."""
    clipped = False
    lines: list[str] = []
    for line in text.splitlines():
        rendered = truncate_line(line, max_chars)
        clipped = clipped or rendered != line
        lines.append(rendered)

    rendered, dropped = cap_lines(lines, max_chars)

    return rendered, dropped, clipped


def _dropped_hint(dropped: int) -> str:
    """The note that admits how many printed lines the budget left out."""
    return hint_suffix(
        [f"{dropped} more printed {pluralize(dropped, 'line')}"] if dropped else []
    )


def _diagnostic(exc: MontyError, printed: str, max_chars: int) -> str:
    """Render a sandbox failure as text the model can repair its program from.

    A traceback is the whole correction for a program the model wrote itself,
    so the three error classes that carry one render it in full. Output printed
    before the failure leads, since a program that reports its own progress
    says more about where it went wrong than the frame the interpreter stopped
    in.

    A typing error is the cheapest of the three, since it arrives before the
    program ran at all: nothing was read, nothing was staged, and the
    diagnostic names the field or argument the injected stub disagrees with.
    """
    detail = (
        exc.display()
        if isinstance(exc, MontySyntaxError | MontyRuntimeError | MontyTypingError)
        else str(exc)
    )
    diagnostic = detail
    if printed := printed.rstrip("\n"):
        diagnostic = f"Printed before the failure:\n{printed}\n\n{detail}"

    return truncate_middle(diagnostic, max_chars)


@dataclass(slots=True, frozen=True)
class PythonResult:
    """What one program produced: its value and anything it printed."""

    result: str | None = None
    """The trailing expression's value, or ``None`` when there was none."""

    stdout: str = ""
    truncated: bool = False
    """Whether printed output was cut to fit the budget."""

    script_path: str | None = None
    """Canonical workspace path when the program came from a stored script."""

    changeset: ChangesetOutcome | None = None
    """What the program changed in a gated root, applied or awaiting ``apply_changes``."""


@dataclass(slots=True, frozen=True)
class RunPythonTool(AsyncPathTool[PythonResult]):
    """Run a Python program in a sandbox that reaches nothing outside itself.

    Each call takes a fresh session out of the pool, so one program never sees
    another's variables, and a session a time limit stopped mid-operation,
    which Monty leaves with no guarantees about its heap, is never fed again.
    """

    pool: AsyncMonty = field(kw_only=True)
    changeset_limits: ChangesetLimits = field(kw_only=True)

    writable: tuple[SearchPath, ...] = field(default=(), kw_only=True)
    """The roots a program may change, narrower than the ones it reads.

    Only the direct ones (``/tmp``) in a mode that may not write the workspace,
    which is what makes every change to it from inside a program refuse
    exactly where ``write_document`` does.
    """

    commit: CommitChanges | None = field(default=None, kw_only=True)
    """Writes what a program changed in a direct root, and applies or stages the rest."""

    environ: Mapping[str, str] = field(default_factory=dict, kw_only=True)
    """Variables beside the ones every run has, such as ``HOME`` and ``USER``.

    Decided where the tool is built, since who runs it and which workspace is
    theirs is a property of the run rather than of the sandbox.
    """

    surface: MontySurface = field(default=MontySurface(), kw_only=True)
    """The tools a program may call, and the stub that declares them.

    Empty by default, which is the sandbox as it was: the mount and nothing
    else.  What is in it is decided where the tool is built, since whether a
    tool is live is a property of the run rather than of the sandbox.  A frozen
    empty surface is safe to share as the default, since nothing mutates one.
    """

    type_check: bool = field(default=True, kw_only=True)
    """Whether a program is type-checked against :attr:`surface` before it runs.

    A misread result shape costs a diagnostic that names the field rather than a
    run that reads documents and then fails, and a forgotten ``await`` on an
    injected tool is reported as such instead of as a coroutine that is not
    subscriptable two lines later.

    It is a whole type checker rather than a check of the stub, so it also
    rejects unsound code that would have run: ``Path(os.getenv("TMPDIR"))``, for
    the ``str | None`` that is, where the program should name ``/tmp`` outright
    as the instructions already say.  That is the trade, and it is why the
    surface declares the mount's ``open`` too.
    """

    limits: ResourceLimits = field(default_factory=_default_limits)
    max_output_chars: int = 20_000
    max_document_chars: int = 5_000_000
    """Cap on any one document this run reads or writes.

    Decoding happens here, in the server process, before the text is handed to
    the sandbox, so :attr:`limits`' memory budget does not cover it: that one
    counts what the interpreter allocates inside itself.  What the host holds
    is one document at a time, so that is what this bounds, and the mount
    applies the same cap to every document the program opens.
    """

    def _stubs(self) -> str:
        """What the checker is given: the mount's declarations and the surface's.

        Joined here because this is where both are in hand, and the mount's
        half stands whether or not a tool was injected: without it every
        program that opens a document is rejected before it runs.
        """
        return "\n\n".join(filter(None, (MOUNT_STUB, self.surface.stubs)))

    @override
    async def __call__(
        self,
        code: CodeArg = None,
        script_path: PythonScriptPathArg = None,
    ) -> ToolOutput[PythonResult]:
        """Run a short program in the Monty interpreter.

        Provide exactly one of `code` or `script_path` on every call.

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
        output through a file: a tool called here needs no `output_path`, no
        read back, and no guessing at the shape of what was written.

        End the program with the expression whose value you want back, and
        print anything else worth seeing.

        The workspace behaves like a normal filesystem: a program may write,
        append, rename, and remove files and directories with the ordinary
        `Path` and `open` calls.  Nothing changes while it runs, its reads
        see its own changes, and once it succeeds every change to `/tmp` is
        written and every other change is staged as one changeset that
        `apply_changes` applies after the user approves it.
        """
        source, canonical_script = await asyncio.to_thread(self._prepare, code, script_path)
        filesystem = self._filesystem()
        printed = CollectString()

        async with self.pool.checkout(
            script_name=canonical_script or "script.py",
            limits=self.limits,
            type_check=self.type_check,
            type_check_stubs=self._stubs(),
            type_check_format="concise",
        ) as session:
            try:
                value = await session.feed_run(
                    source,
                    print_callback=printed,
                    cwd=str(WORKSPACE_MOUNT),
                    os=filesystem,
                    external_lookup=dict(self.surface.external_lookup),
                )

            # The pool itself is gone, which no rewrite of the program fixes.
            except (MontyShutdown, MontyDisconnectError):
                raise

            except MontyError as exc:
                raise ToolRetry(
                    _diagnostic(exc, printed.output, self.max_output_chars)
                ) from exc

        # Only once the program succeeded, so one that fails changes nothing.
        changes = await asyncio.to_thread(stage_changes, filesystem)

        if changes is not None and self.commit is None:
            raise ToolRetry("This run keeps none of the changes a program makes.")

        changeset = None if changes is None or self.commit is None else await self.commit(changes)

        return self._output(
            value, printed.output, script_path=canonical_script, changeset=changeset
        )

    def _prepare(self, code: str | None, script_path: str | None) -> tuple[str, str | None]:
        """Resolve the program to run and the canonical script it came from, off the event loop.

        Inline ``code`` answers to the same cap a stored script's read does,
        which no read ever sizes.
        """
        code, script_path = validate_program_source(code, script_path)

        source, canonical_script = code or "", None
        if script_path is not None:
            sp, local, absolute = resolve_file_or_retry(self.resolved_paths, script_path)
            canonical_script = sp.prefixed(local)

            if not is_python_script(canonical_script):
                raise ToolRetry(
                    f"'{canonical_script}' is not a `.py` script. `script_path` "
                    "names the program to run, not a document it reads."
                )

            # Sized before it is decoded, so a file that cannot fit is refused
            # without ever being read into memory.
            check_read_budget(
                canonical_script, absolute.stat().st_size, self.max_document_chars
            )
            source = read_text_or_retry(
                absolute, canonical_script, sidecar_hint(canonical_script)
            ).text

        if len(source) > self.max_document_chars:
            raise ToolRetry(
                f"The program is too large for one Python run ({len(source)} "
                f"characters, maximum {self.max_document_chars})."
            )

        return source, canonical_script

    def _filesystem(self) -> WorkspaceOS:
        """Build the filesystem: the roots mounted, the environment beside them.

        The environment mirrors an ordinary shell's, so ``PWD`` agrees with
        ``os.getcwd()``.
        """
        environ = {"PWD": str(WORKSPACE_MOUNT), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}

        return WorkspaceOS(
            paths=self.resolved_paths,
            inner=OSAccess([], environ=environ | dict(self.environ)),
            limits=self.changeset_limits,
            writable=self.writable,
            max_document_chars=self.max_document_chars,
        )

    def _output(
        self,
        value: object,
        printed: str,
        *,
        script_path: str | None,
        changeset: ChangesetOutcome | None,
    ) -> ToolOutput[PythonResult]:
        """Budget what the program produced and render it for the model.

        The value is what the call was for, so it keeps the whole output budget
        to itself and printed output is bounded separately.
        """
        stdout, dropped, clipped = _budget_lines(printed, self.max_output_chars)
        result = (
            None
            if value is None
            else truncate_line(_VALUE_REPR.repr(value), self.max_output_chars)
        )

        parts = [stdout] if stdout else []
        if result is not None:
            parts.append(f"Result: {result}")

        body = "\n".join(parts) or "The program printed nothing and returned no value."

        return ToolOutput(
            data=PythonResult(
                result=result,
                stdout=stdout,
                truncated=clipped or bool(dropped),
                script_path=script_path,
                changeset=changeset,
            ),
            formatted="\n\n".join(
                part
                for part in (body + _dropped_hint(dropped), _changes_report(changeset))
                if part
            ),
        )


def _changes_report(changeset: ChangesetOutcome | None) -> str:
    """Tell the model what the program changed and what is left to do about it."""
    match changeset:
        case None:
            return ""
        case PendingChanges(changeset_id=changeset_id, summary=summary):
            changes = f"{summary.count} {pluralize(summary.count, 'change')}"
            lines = "\n".join(summary.lines())

            return (
                f"Staged {changes}, and nothing in the workspace has changed yet:\n"
                f"{lines}\nCall apply_changes with changeset_id='{changeset_id}' to "
                "apply them, and the user approves them there. Do not run the program "
                "again unless that call says the changeset is stale."
            )
        case _:
            count = len(changeset.reports)
            reports = "\n".join(changeset.reports)

            return f"Applied {count} {pluralize(count, 'change')} to the workspace:\n{reports}"
