"""Run small Python programs in the Monty sandbox."""

import asyncio
import reprlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from stat import S_ISREG
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
from ..text import MAX_BYTES_PER_CHAR
from .base import (
    AsyncPathTool,
    ToolOutput,
    ToolRetry,
    entry_stat,
    read_text_or_retry,
    resolve_file_or_retry,
    sidecar_hint,
)
from .formatting import cap_lines, hint_suffix, truncate_line, truncate_middle
from .mutations import WriteDocumentTool
from .sink import resolve_output_target

__all__ = [
    "SANDBOX_TMP_DIR",
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
            "repaired with `edit_document` and run again."
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
            "path is also given as `output_path`."
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
            "Interactive calls require approval before this write."
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

    written_file: str | None = None
    """Canonical workspace path persisted after the program completed."""


@dataclass(slots=True, frozen=True)
class _PreparedRun:
    """Resolved source and private files for one sandbox execution.

    ``files`` holds the text of every path that exists.  The one path that may
    be absent is the declared ``output``, which a program is allowed to create,
    so its absence is simply a missing key.
    """

    source: str
    script_path: str | None
    files: dict[str, str]
    output: str | None


def _virtual_path(canonical_path: str) -> str:
    """Map a canonical workspace path into Monty's isolated filesystem."""
    return str(PurePosixPath("/workspace") / canonical_path)


SANDBOX_TMP_DIR = PurePosixPath("/tmp")
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
        belong in ``/tmp`` (also ``TMPDIR``), which exists from the start and is
        discarded when the call ends. Only the declared output file can persist,
        and only after the program succeeds; name it under a `.scratch/`
        directory to carry state to a later call without adding a document. Only ``asyncio``,
        ``collections``, ``dataclasses``,
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
        filesystem = self._filesystem(prepared)
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

        written_file, write_report = await self._persist_output(prepared, filesystem)

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

        source, canonical_script = code or "", None
        if script_path is not None:
            canonical_script, absolute = self._resolve_input(script_path)
            if PurePosixPath(canonical_script).suffix.lower() != ".py":
                raise ToolRetry(f"'{canonical_script}' is not a `.py` script.")

            source = self._read(canonical_script, absolute, 0) or ""

        subject = "The script and workspace inputs are"
        staged = len(source)
        self._check_budget(staged, subject)

        # Every path is resolved before any file is read, so one path serving as
        # both an input and the output is read, decoded, and budgeted once.
        output: tuple[str, Path] | None = None
        if output_path is not None:
            _sink, canonical, absolute = resolve_output_target(self.writer, output_path)
            output = (canonical, absolute)
        targets = [self._resolve_input(raw) for raw in input_paths]
        if output is not None:
            targets.append(output)

        # The budget is charged file by file rather than once at the end: it
        # bounds host memory, which a check that runs only after every input has
        # already been decoded no longer does.
        files: dict[str, str] = {}
        for canonical, absolute in targets:
            if canonical in files:
                continue

            text = self._read(canonical, absolute, staged)
            if text is None:
                continue

            files[canonical] = text
            staged += len(text)
            self._check_budget(staged, subject)

        return _PreparedRun(
            source=source,
            script_path=canonical_script,
            files=files,
            output=None if output is None else output[0],
        )

    def _resolve_input(self, file_path: str) -> tuple[str, Path]:
        """Resolve one readable workspace file, which must already exist."""
        sp, local, absolute = resolve_file_or_retry(self.resolved_paths, file_path)
        return sp.prefixed(local), absolute

    def _read(
        self, canonical_path: str, absolute_path: Path, staged: int
    ) -> str | None:
        """Decode a resolved workspace file, or ``None`` when it does not exist.

        Only the declared output can be missing: an input is resolved through
        the reader, which refuses a path naming no file.

        *staged* is the character count already charged to the run. A file is
        sized before it is decoded, so one that cannot fit whatever it decodes
        to is refused without ever being read into memory: a file above
        :data:`~hivegent.text.MAX_BYTES_PER_CHAR` times the remaining budget is
        over it for certain, while anything smaller is left to the exact check
        on the decoded text.
        """
        st = entry_stat(absolute_path)
        if st is None or not S_ISREG(st.st_mode):
            return None

        remaining = self.max_workspace_chars - staged
        if st.st_size > remaining * MAX_BYTES_PER_CHAR:
            raise ToolRetry(
                f"'{canonical_path}' is too large for one Python run "
                f"(the script and workspace inputs may total "
                f"{self.max_workspace_chars} characters)."
            )

        return read_text_or_retry(
            absolute_path, canonical_path, sidecar_hint(canonical_path)
        ).text

    def _check_budget(self, total_chars: int, subject: str) -> None:
        """Refuse text too large to stage into, or commit out of, one run."""
        if total_chars <= self.max_workspace_chars:
            return

        raise ToolRetry(
            f"{subject} too large for one Python run ({total_chars} characters, "
            f"maximum {self.max_workspace_chars})."
        )

    @staticmethod
    def _filesystem(prepared: _PreparedRun) -> OSAccess:
        """Create the private filesystem, preserving a missing output as missing."""
        filesystem = OSAccess(
            [
                MemoryFile(_virtual_path(path), text)
                for path, text in prepared.files.items()
            ],
            environ={"TMPDIR": str(SANDBOX_TMP_DIR)},
        )
        filesystem.path_mkdir(SANDBOX_TMP_DIR, parents=True, exist_ok=True)
        if prepared.output is not None and prepared.output not in prepared.files:
            filesystem.path_mkdir(
                PurePosixPath(_virtual_path(prepared.output)).parent,
                parents=True,
                exist_ok=True,
            )

        return filesystem

    async def _persist_output(
        self, prepared: _PreparedRun, filesystem: OSAccess
    ) -> tuple[str | None, str | None]:
        """Commit one changed text output through the workspace mutation gateway."""
        if prepared.output is None:
            return None, None

        original = prepared.files.get(prepared.output)
        virtual_path = PurePosixPath(_virtual_path(prepared.output))
        if not filesystem.path_is_file(virtual_path):
            if original is None:
                return None, "The declared workspace output was not created."

            raise ToolRetry("The declared output cannot be deleted or renamed.")

        try:
            content = filesystem.path_read_text(virtual_path)
        except UnicodeDecodeError as exc:
            raise ToolRetry("The declared output must contain UTF-8 text.") from exc

        self._check_budget(len(content), "The declared workspace output is")

        if content == original:
            return None, "The declared workspace output was unchanged."

        assert self.writer is not None
        result = await self.writer(
            prepared.output,
            content,
            "replace" if original is not None else "create",
            content_hash(original) if original is not None else None,
        )

        return prepared.output, result.text

    def _output(
        self,
        value: object,
        printed: str,
        *,
        script_path: str | None,
        written_file: str | None,
        write_report: str | None,
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
