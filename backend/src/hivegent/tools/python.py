"""Run small Python programs in the Monty sandbox."""

import asyncio
import reprlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Annotated, override

from pydantic import Field
from pydantic_monty import (
    AsyncMonty,
    CollectString,
    MemoryFile,
    MontyDisconnectError,
    MontyError,
    MontyRuntimeError,
    MontyShutdown,
    MontySyntaxError,
    OSAccess,
    ResourceLimits,
)

from ..config import content_hash
from ..humanize import pluralize
from .base import (
    WORKSPACE_PATH_HINT,
    AsyncPathTool,
    ToolOutput,
    ToolRetry,
    read_text_or_retry,
    resolve_file_or_retry,
    sidecar_hint,
)
from .formatting import cap_lines, hint_suffix, truncate_line
from .mutations import WriteDocumentTool, resolve_mutation_target

__all__ = [
    "SCRATCH_DIR",
    "CodeArg",
    "PythonInputPathsArg",
    "PythonOutputPathArg",
    "PythonResult",
    "PythonScriptPathArg",
    "RunPythonTool",
]

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
            "Inline program to run in Monty. Provide either this or "
            "`script_path`, but not both. Its trailing expression is the value "
            "returned to you, and whatever it prints comes back alongside."
        ),
    ),
]
PythonScriptPathArg = Annotated[
    str | None,
    Field(
        description=(
            "Full workspace path of a `.py` script to run instead of inline "
            "code. The current file is loaded on every call, so it can be "
            f"repaired with `edit_document` and run again. {WORKSPACE_PATH_HINT}"
        ),
    ),
]
PythonInputPathsArg = Annotated[
    tuple[str, ...],
    Field(
        description=(
            "Full workspace paths of text files to expose as private in-memory "
            "copies. A path such as `~/data.json` is available to the program "
            "at `/workspace/~/data.json`. Changes are discarded unless that "
            f"path is also given as `output_path`. {WORKSPACE_PATH_HINT}"
        ),
        max_length=20,
    ),
]
PythonOutputPathArg = Annotated[
    str | None,
    Field(
        description=(
            "One full workspace path whose in-memory file may be created or "
            "changed by the program and persisted after a successful run. It "
            "uses the same `/workspace/<full path>` virtual path as inputs. "
            "Interactive calls require approval before this write. "
            f"{WORKSPACE_PATH_HINT}"
        ),
    ),
]


def _default_limits() -> ResourceLimits:
    """The budget one program runs under when no caller sets one."""
    return {"max_duration_secs": 5.0, "max_memory": 256_000_000}


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
    so the two error classes that carry one render it in full. Output printed
    before the failure leads, since a program that reports its own progress
    says more about where it went wrong than the frame the interpreter stopped
    in.
    """
    detail = (
        exc.display()
        if isinstance(exc, MontySyntaxError | MontyRuntimeError)
        else str(exc)
    )
    printed, dropped, _clipped = _budget_lines(printed.rstrip("\n"), max_chars)

    if not printed:
        return detail

    note = _dropped_hint(dropped)

    return f"Printed before the failure:\n{printed}{note}\n\n{detail}"


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

    written_file: str | None = None
    """Canonical workspace path persisted after the program completed."""


@dataclass(slots=True, frozen=True)
class _WorkspaceFile:
    """One scoped workspace text file staged in Monty's private filesystem."""

    canonical_path: str
    original: str | None


@dataclass(slots=True, frozen=True)
class _PreparedRun:
    """Resolved source and private files for one sandbox execution."""

    source: str
    script_path: str | None
    files: tuple[_WorkspaceFile, ...]
    output: _WorkspaceFile | None


def _virtual_path(canonical_path: str) -> str:
    """Map a canonical workspace path into Monty's isolated filesystem."""
    return str(PurePosixPath("/workspace") / canonical_path)


SCRATCH_DIR = PurePosixPath("/tmp")
"""Scratch directory every run starts with, also named by ``TMPDIR``.

Monty has no ``tempfile`` module and no working directory, so a program with an
intermediate to park has nowhere to put it unless the directory already exists:
a bare write to ``/tmp`` fails with ``FileNotFoundError``.  Creating it here and
naming it in the environment makes ``os.getenv("TMPDIR")`` the one answer, the
way the workspace prefix is the one answer for a document.  It is part of the
private filesystem, so it is discarded with everything else a run did not
declare as its output.
"""


@dataclass(slots=True, frozen=True)
class RunPythonTool(AsyncPathTool[PythonResult]):
    """Run a Python program in a sandbox that reaches nothing outside itself.

    Each call takes a fresh session out of the pool, so one program never sees
    another's variables and never inherits what another spent of the budget:
    :attr:`limits` caps execution time cumulatively per session, which would
    otherwise leave a session that once looped failing every later call.
    """

    pool: AsyncMonty = field(kw_only=True)
    writer: WriteDocumentTool | None = field(default=None, kw_only=True)
    limits: ResourceLimits = field(default_factory=_default_limits)
    max_output_chars: int = 20_000
    max_workspace_chars: int = 5_000_000
    """Host memory one run may spend on workspace text, in and out.

    The private copies are built here, in the server process, before they are
    handed to the sandbox, so :attr:`limits`' memory budget does not cover
    them: that one counts what the interpreter allocates inside itself.  A
    read tool holds one file at a time, but a run stages up to twenty at once,
    which is the exposure this bounds.
    """

    @override
    async def __call__(
        self,
        code: CodeArg = None,
        script_path: PythonScriptPathArg = None,
        input_paths: PythonInputPathsArg = (),
        output_path: PythonOutputPathArg = None,
    ) -> ToolOutput[PythonResult]:
        """Run a short program in the Monty interpreter.

        Reach for it whenever an answer turns on arithmetic, dates, sorting, or
        counting, rather than working the result out in your head. Monty
        supports only a subset of Python and its standard library, it is not a
        CPython environment. The program has no network or host filesystem
        access. Inputs can be literals or explicitly named workspace text files,
        which are private in-memory copies under ``/workspace``. Intermediates
        belong in ``/tmp`` (also ``TMPDIR``), which exists from the start and
        is discarded when the call ends. Only the declared output file can
        persist, and only after the program succeeds. Only ``asyncio``, ``collections``, ``dataclasses``,
        ``datetime``, ``functools``, ``itertools``, ``json``, ``math``, ``os``,
        ``pathlib``, ``re``, ``sys``, ``typing``, and ``unicodedata`` can be
        imported. There is no numpy or pandas, and classes cannot inherit. End
        the program with the expression whose value you want back, and print
        anything else worth seeing.
        """
        prepared = await asyncio.to_thread(
            self._prepare,
            code,
            script_path,
            input_paths,
            output_path,
        )
        filesystem = self._filesystem(prepared.files, prepared.output)
        printed = CollectString()

        async with self.pool.checkout(
            script_name=prepared.script_path or "script.py",
            limits=self.limits,
        ) as session:
            try:
                value = await session.feed_run(
                    prepared.source,
                    print_callback=printed,
                    os=filesystem,
                )

            # The pool itself is gone, which no rewrite of the program fixes.
            except (MontyShutdown, MontyDisconnectError):
                raise

            except MontyError as exc:
                raise ToolRetry(
                    _diagnostic(exc, printed.output, self.max_output_chars)
                ) from exc

        written_file, write_report = await self._persist_output(
            prepared.output, filesystem
        )

        return self._output(
            value,
            printed.output,
            script_path=prepared.script_path,
            written_file=written_file,
            write_report=write_report,
        )

    def _prepare(
        self,
        code: str | None,
        script_path: str | None,
        input_paths: tuple[str, ...],
        output_path: str | None,
    ) -> _PreparedRun:
        """Resolve and decode all workspace material outside the event loop."""
        if (code is None) == (script_path is None):
            raise ToolRetry("Provide exactly one of `code` or `script_path`.")

        if code is not None:
            source = code
            canonical_script = None
        else:
            assert script_path is not None
            script = self._read_file(script_path)
            if PurePosixPath(script.canonical_path).suffix.lower() != ".py":
                raise ToolRetry(f"'{script.canonical_path}' is not a `.py` script.")

            source = script.original or ""
            canonical_script = script.canonical_path

        # Every path is resolved before any file is read, so one path serving as
        # both an input and the output is read, decoded, and budgeted once.
        output_target = (
            None if output_path is None else self._resolve_output(output_path)
        )
        targets = [self._resolve_input(raw) for raw in input_paths]
        if output_target is not None:
            targets.append(output_target)

        staged: dict[str, _WorkspaceFile] = {}
        for canonical, absolute in targets:
            if canonical in staged:
                continue

            staged[canonical] = self._read_workspace_file(canonical, absolute)

        self._check_budget(
            len(source) + sum(len(item.original or "") for item in staged.values()),
            "The script and workspace inputs are",
        )

        return _PreparedRun(
            source=source,
            script_path=canonical_script,
            files=tuple(staged.values()),
            output=None if output_target is None else staged[output_target[0]],
        )

    def _resolve_input(self, file_path: str) -> tuple[str, Path]:
        """Resolve one readable workspace file, which must already exist."""
        sp, local, absolute = resolve_file_or_retry(self.resolved_paths, file_path)
        return sp.prefixed(local), absolute

    def _resolve_output(self, output_path: str) -> tuple[str, Path]:
        """Resolve the one writable output, which need not exist yet.

        Routed through the resolver the commit itself runs through, so a path
        the write would turn away is turned away here, in the same words, before
        the program runs.
        """
        if self.writer is None:
            raise ToolRetry(
                "Writing to the workspace is not available to this tool, so "
                "`output_path` cannot be used."
            )

        canonical, _local, absolute = resolve_mutation_target(
            self.writer.resolved_paths, output_path
        )
        if absolute.is_dir():
            raise ToolRetry(f"'{canonical}' is a directory.")

        return canonical, absolute

    def _read_file(self, file_path: str) -> _WorkspaceFile:
        """Resolve and decode one existing readable workspace file."""
        return self._read_workspace_file(*self._resolve_input(file_path))

    @staticmethod
    def _read_workspace_file(
        canonical_path: str, absolute_path: Path
    ) -> _WorkspaceFile:
        """Decode a resolved workspace file, or record that it does not exist.

        Only the declared output can be missing: an input is resolved through
        the reader, which refuses a path naming no file.
        """
        if not absolute_path.is_file():
            return _WorkspaceFile(canonical_path=canonical_path, original=None)

        decoded = read_text_or_retry(
            absolute_path, canonical_path, sidecar_hint(canonical_path)
        )
        return _WorkspaceFile(canonical_path=canonical_path, original=decoded.text)

    def _check_budget(self, total_chars: int, subject: str) -> None:
        """Refuse text too large to stage into, or commit out of, one run."""
        if total_chars <= self.max_workspace_chars:
            return

        raise ToolRetry(
            f"{subject} too large for one Python run ({total_chars} characters, "
            f"maximum {self.max_workspace_chars})."
        )

    @staticmethod
    def _filesystem(
        staged: tuple[_WorkspaceFile, ...], output: _WorkspaceFile | None
    ) -> OSAccess:
        """Create the private filesystem, preserving a missing output as missing."""
        filesystem = OSAccess(
            [
                MemoryFile(_virtual_path(item.canonical_path), item.original)
                for item in staged
                if item.original is not None
            ],
            environ={"TMPDIR": str(SCRATCH_DIR)},
        )
        filesystem.path_mkdir(SCRATCH_DIR, parents=True, exist_ok=True)
        if output is not None and output.original is None:
            filesystem.path_mkdir(
                PurePosixPath(_virtual_path(output.canonical_path)).parent,
                parents=True,
                exist_ok=True,
            )

        return filesystem

    async def _persist_output(
        self, output: _WorkspaceFile | None, filesystem: OSAccess
    ) -> tuple[str | None, str | None]:
        """Commit one changed text output through the workspace mutation gateway."""
        if output is None:
            return None, None

        virtual_path = PurePosixPath(_virtual_path(output.canonical_path))
        file = next(
            (
                candidate
                for candidate in filesystem.files
                if not candidate.deleted and candidate.path == virtual_path
            ),
            None,
        )
        if file is None:
            if output.original is None:
                return None, "The declared workspace output was not created."

            raise ToolRetry("The declared output cannot be deleted or renamed.")

        content = file.read_content()
        if not isinstance(content, str):
            raise ToolRetry("The declared output must be written as text, not bytes.")

        self._check_budget(len(content), "The declared workspace output is")

        if output.original is not None and content == output.original:
            return None, "The declared workspace output was unchanged."

        assert self.writer is not None
        mode = "replace" if output.original is not None else "create"
        expected_hash = (
            content_hash(output.original) if output.original is not None else None
        )
        result = await self.writer(
            output.canonical_path,
            content,
            mode,
            expected_hash,
        )

        return output.canonical_path, result.text

    def _output(
        self,
        value: object,
        printed: str,
        *,
        script_path: str | None = None,
        written_file: str | None = None,
        write_report: str | None = None,
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
                written_file=written_file,
            ),
            formatted="\n\n".join(
                part for part in (body + _dropped_hint(dropped), write_report) if part
            ),
        )
