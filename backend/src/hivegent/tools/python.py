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
    AsyncPathTool,
    ToolOutput,
    ToolRetry,
    check_read_budget,
    read_text_or_retry,
    resolve_file_or_retry,
    sidecar_hint,
)
from .formatting import cap_lines, hint_suffix, truncate_line, truncate_middle
from .mutations import WriteDocumentTool
from .sink import resolve_output_target
from .workspace_os import WorkspaceOS

__all__ = [
    "SANDBOX_OUTPUT_FILE",
    "SANDBOX_TMP_DIR",
    "CodeArg",
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

SANDBOX_TMP_DIR = PurePosixPath("/tmp")
"""Scratch directory every run starts with, also named by ``TMPDIR``.

Monty has no ``tempfile`` module and no working directory, so a program with an
intermediate to park has nowhere to put it unless the directory already exists:
a bare write to ``/tmp`` fails with ``FileNotFoundError``.  Creating it here and
naming it in the environment makes ``os.getenv("TMPDIR")`` the one answer, the
way the workspace prefix is the one answer for a document.  It is discarded
when the call ends, whether the program succeeded or not.
"""

SANDBOX_OUTPUT_FILE = PurePosixPath("/output")
"""Where a program writes the one document the call may persist.

The mounted workspace is read-only outside `.scratch/`, because committing a
document runs the async mutation gateway and needs a human's answer, neither of
which a synchronous filesystem callback can reach.  So the program writes here
instead and the tool commits it afterwards, which is also what makes the commit
conditional on the program having succeeded.  Named by ``OUTPUT`` in the
environment, the way ``/tmp`` is named by ``TMPDIR``.
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
PythonOutputPathArg = Annotated[
    str | None,
    Field(
        description=(
            "Full workspace path to persist the program's `/output` file to "
            "after a successful run. The mounted workspace is read-only, so "
            "this is how a program writes a document. Interactive calls "
            "require approval before this write."
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
    """The program to run and the one document approved before it starts.

    Nothing is staged: the workspace is mounted, so the program reads it where
    it lies.  ``output`` is the path ``/output`` will be committed to, resolved
    and fingerprinted here so a version landing while the program runs is
    refused by the gateway rather than silently overwritten.
    """

    source: str
    script_path: str | None
    output: str | None = None
    expected_hash: str | None = None
    """Fingerprint of the output document as it stood before the run.

    ``None`` when nothing stood there, which is also what makes the commit a
    ``create`` that refuses to absorb a document appearing in between.
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
    """Commits the declared output, and names the span a program may write.

    ``None`` in a mode that may not write, which is what makes a `.scratch/`
    write from inside a program refuse exactly where ``write_document`` does.
    """

    limits: ResourceLimits = field(default_factory=_default_limits)
    max_output_chars: int = 20_000
    max_document_chars: int = 5_000_000
    """Cap on any one document this run reads or commits.

    Decoding happens here, in the server process, before the text is handed to
    the sandbox, so :attr:`limits`' memory budget does not cover it: that one
    counts what the interpreter allocates inside itself.  What the host holds
    is one document at a time, so that is what this bounds, and the mount
    applies the same cap to every document the program opens.
    """

    @override
    async def __call__(
        self,
        code: CodeArg = None,
        script_path: PythonScriptPathArg = None,
        output_path: PythonOutputPathArg = None,
    ) -> ToolOutput[PythonResult]:
        """Run a short program in the Monty interpreter.

        Reach for it whenever an answer turns on arithmetic, dates, sorting,
        counting, or reading more documents than an answer needs quoting from.
        Monty supports only a subset of Python and its standard library, it is
        not a CPython environment, and the program has no network or host
        filesystem access. The whole workspace is mounted read-only at
        ``/workspace``, so a document is ``/workspace`` plus its full path and
        the program may open a path it discovers while running. Intermediates
        belong in ``/tmp`` (also ``TMPDIR``), which exists from the start and
        is discarded when the call ends, while state that has to outlive the
        call is written straight to a `.scratch/` path in the workspace. To
        persist a document, write ``/output`` (also ``OUTPUT``) and name where
        it goes as ``output_path``, which is committed only after the program
        succeeds. Only part of the standard library is implemented and a
        missing import says so by name, so try one rather than assuming: there
        is no ``glob`` or ``fnmatch``, no numpy or pandas, and classes cannot
        inherit. End the program with the expression whose value you want back,
        and print anything else worth seeing.
        """
        prepared = await asyncio.to_thread(
            self._prepare,
            code,
            script_path,
            output_path,
        )
        filesystem = self._filesystem()
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
        output_path: str | None,
    ) -> _PreparedRun:
        """Resolve the program and the approved output, off the event loop.

        Nothing is staged: the mount reads the workspace where it lies, so all
        this settles is which source runs and which document the run was given
        permission to persist.
        """
        if (code is None) == (script_path is None):
            raise ToolRetry("Provide exactly one of `code` or `script_path`.")

        source, canonical_script = code or "", None
        if script_path is not None:
            canonical_script, absolute = self._resolve_script(script_path)
            if PurePosixPath(canonical_script).suffix.lower() != ".py":
                raise ToolRetry(f"'{canonical_script}' is not a `.py` script.")

            source = self._read(canonical_script, absolute)

        self._check_budget(len(source), "The program")
        if output_path is None:
            return _PreparedRun(source, canonical_script)

        _sink, canonical, absolute = resolve_output_target(self.writer, output_path)

        return _PreparedRun(
            source, canonical_script, canonical, self._basis(canonical, absolute)
        )

    def _basis(self, canonical_path: str, absolute_path: Path) -> str | None:
        """Fingerprint the output document as it stands before the run.

        The guard the write tools take from a prior read, taken here from the
        state the program is about to work from: the commit lands after the
        program ends, so a version that arrives in between would otherwise be
        overwritten without a word.  ``None`` when the file is not there, where
        ``create`` is the guard instead.

        The text is dropped as soon as it is hashed, so what it costs is the
        decode, bounded like every other.
        """
        if not absolute_path.is_file():
            return None

        return content_hash(self._read(canonical_path, absolute_path))

    def _resolve_script(self, file_path: str) -> tuple[str, Path]:
        """Resolve the stored program, which must already exist."""
        sp, local, absolute = resolve_file_or_retry(self.resolved_paths, file_path)
        return sp.prefixed(local), absolute

    def _read(self, canonical_path: str, absolute_path: Path) -> str:
        """Decode one workspace document, bounded as the mount bounds its own.

        Sized before it is decoded, so a file that cannot fit whatever it
        decodes to is refused without ever being read into memory.  The two
        documents a call reads for itself, the stored script and the output it
        fingerprints, answer to the same cap the program's own reads do.
        """
        check_read_budget(
            canonical_path, absolute_path.stat().st_size, self.max_document_chars
        )

        return read_text_or_retry(
            absolute_path, canonical_path, sidecar_hint(canonical_path)
        ).text

    def _check_budget(self, total_chars: int, subject: str) -> None:
        """Refuse text too large to run inside, or commit out of, one run.

        The exact counterpart of :func:`check_read_budget`, which bounds a file
        before it is decoded: this is the same cap checked on text already in
        hand, which covers inline ``code`` and the committed output, neither of
        which any read ever sizes.
        """
        if total_chars <= self.max_document_chars:
            return

        raise ToolRetry(
            f"{subject} is too large for one Python run ({total_chars} "
            f"characters, maximum {self.max_document_chars})."
        )

    def _filesystem(self) -> WorkspaceOS:
        """Build the filesystem: the workspace mounted, the run's own beside it.

        ``inner`` owns ``/tmp`` and ``/output``, both of which exist from the
        start so a bare write to either lands rather than failing on a missing
        parent, and both of which disappear with the session.
        """
        inner = OSAccess(
            [],
            environ={
                "TMPDIR": str(SANDBOX_TMP_DIR),
                "OUTPUT": str(SANDBOX_OUTPUT_FILE),
            },
        )
        inner.path_mkdir(SANDBOX_TMP_DIR, parents=True, exist_ok=True)

        return WorkspaceOS(
            paths=self.resolved_paths,
            inner=inner,
            writable=() if self.writer is None else self.writer.resolved_paths,
            max_document_chars=self.max_document_chars,
        )

    async def _persist_output(
        self, prepared: _PreparedRun, filesystem: WorkspaceOS
    ) -> tuple[str | None, str | None]:
        """Commit the program's output through the workspace mutation gateway.

        Only after the program succeeded, which is what an OS callback's
        inability to await buys back: a program that fails halfway leaves the
        workspace exactly as it found it, `.scratch/` aside.  A declared output
        the program never wrote is worth a sentence, since silence would leave
        the model believing the request was honoured.
        """
        if prepared.output is None:
            return None, None

        if not filesystem.inner.path_is_file(SANDBOX_OUTPUT_FILE):
            return None, (
                f"Nothing was committed to '{prepared.output}': the program "
                f"wrote no {SANDBOX_OUTPUT_FILE} file."
            )

        content = filesystem.inner.path_read_text(SANDBOX_OUTPUT_FILE)
        self._check_budget(len(content), "The program's output")

        assert self.writer is not None
        result = await self.writer(
            prepared.output,
            content,
            "replace" if prepared.expected_hash is not None else "create",
            prepared.expected_hash,
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
