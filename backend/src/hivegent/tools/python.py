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
from .workspace_os import SANDBOX_OUTPUT_FILE, SANDBOX_TMP_DIR, WorkspaceOS

__all__ = [
    "CodeArg",
    "PythonOutputPathArg",
    "PythonResult",
    "PythonScriptPathArg",
    "RunPythonTool",
    "is_python_script",
]


def is_python_script(file_path: str) -> bool:
    """Return whether *file_path* names a program this tool will run.

    The one table behind the two halves of the stored-program flow, for the
    same reason :func:`~hivegent.converters.is_tabular` is one behind the
    read/query split: the write that points a file at ``script_path`` and the
    run that accepts it have to agree on which files those are, or a receipt
    promises a rerun the tool then refuses.

    >>> is_python_script("~/.scratch/run.PY")
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
            "path. Anything past a few lines belongs in a `.scratch/` `.py` "
            "file run by `script_path`, where a runtime error costs one "
            "edit_document instead of a retyped program."
        ),
    ),
]
PythonScriptPathArg = Annotated[
    str | None,
    Field(
        description=(
            "Full workspace path of a stored `.py` program to run instead of "
            "inline `code`. The file named here is the program, never a "
            "document it reads. It is loaded fresh on every call, so it can be "
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
            "this is how a program writes a document. Unlike `output_path` on "
            "other tools, it captures nothing by itself: the program must "
            "write the text to `/output`, and printed output and the trailing "
            "value are never it. Interactive calls require approval before "
            "this write."
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
    standing: str | None = None
    """The output document's text as the run found it, seeded into ``/output``.

    ``None`` when nothing stood there, which is also the case where the buffer
    starts absent and its absence is what says the program wrote nothing.
    """

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

        Reach for it when an answer turns on arithmetic, dates, sorting, or
        counting, and when one spans more documents than it could quote from:
        one program reads them all and returns the little the answer needs.

        Monty runs a subset of Python and its standard library rather than a
        CPython environment, so an import it lacks says so by name and is
        worth trying rather than working around, and the program reaches
        nothing outside itself but the workspace: no network, no host
        filesystem, and no tools of its own.

        End the program with the expression whose value you want back, and
        print anything else worth seeing.
        """
        prepared = await asyncio.to_thread(
            self._prepare,
            code,
            script_path,
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
        output_path: str | None,
    ) -> _PreparedRun:
        """Resolve the program and the approved output, off the event loop.

        Nothing is staged: the mount reads the workspace where it lies, so all
        this settles is which source runs and which document the run was given
        permission to persist.
        """
        if (code is None) == (script_path is None):
            raise ToolRetry(
                "Provide exactly one of `code`, the program written inline, or "
                "`script_path`, a stored `.py` program to run. Neither names a "
                "document the program reads: it opens those itself."
            )

        source, canonical_script = code or "", None
        if script_path is not None:
            canonical_script, absolute = self._resolve_script(script_path)
            if not is_python_script(canonical_script):
                raise ToolRetry(
                    f"'{canonical_script}' is not a `.py` script. `script_path` "
                    "names the program to run, not a document it reads."
                )

            source = self._read(canonical_script, absolute)

        self._check_budget(len(source), "The program")
        if output_path is None:
            return _PreparedRun(source, canonical_script)

        _sink, canonical, absolute = resolve_output_target(self.writer, output_path)
        standing = self._standing(canonical, absolute)

        return _PreparedRun(
            source,
            canonical_script,
            canonical,
            standing,
            None if standing is None else content_hash(standing),
        )

    def _standing(self, canonical_path: str, absolute_path: Path) -> str | None:
        """The output document as it stands before the run, or ``None`` if absent.

        It is both what the program starts from, since ``/output`` is seeded
        with it, and what the commit is guarded by once hashed: the commit
        lands after the program ends, so a version arriving in between would
        otherwise be overwritten without a word, and ``create`` is the guard
        instead when there was nothing here.

        One read answers both, bounded like every other.
        """
        if not absolute_path.is_file():
            return None

        return self._read(canonical_path, absolute_path)

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

    def _filesystem(self, prepared: _PreparedRun) -> WorkspaceOS:
        """Build the filesystem: the workspace mounted, the run's own beside it.

        ``inner`` owns ``/tmp`` and ``/output``, both of which exist from the
        start so a bare write to either lands rather than failing on a missing
        parent, and both of which disappear with the session.

        ``/output`` is seeded with the declared document as it stands, which is
        what makes it the same file as the path that names it: a program opens
        either one, reads what is there, appends to it or replaces it, and gets
        what any filesystem would give it.  Seeded from the read the basis was
        taken from, so being able to start from the document costs nothing.
        """
        inner = OSAccess(
            [],
            environ={
                "TMPDIR": str(SANDBOX_TMP_DIR),
                "OUTPUT": str(SANDBOX_OUTPUT_FILE),
            },
        )
        inner.path_mkdir(SANDBOX_TMP_DIR, parents=True, exist_ok=True)
        if prepared.standing is not None:
            _ = inner.path_write_text(SANDBOX_OUTPUT_FILE, prepared.standing)

        return WorkspaceOS(
            paths=self.resolved_paths,
            inner=inner,
            writable=() if self.writer is None else self.writer.resolved_paths,
            output=prepared.output,
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
        the model believing the request was honoured, and a seeded buffer it
        never changed is the same silence: the document is already what the
        commit would write, so saying so beats a version that only bumps the
        mtime and re-indexes.

        The sentence about the missing file also says what to write instead,
        since ``output_path`` captures the result on every other tool and a
        model carrying that meaning over computes the document, returns it, and
        is told only that nothing happened.
        """
        if prepared.output is None:
            return None, None

        if not filesystem.inner.path_is_file(SANDBOX_OUTPUT_FILE):
            return None, (
                f"Nothing was committed to '{prepared.output}': the program "
                f"wrote no {SANDBOX_OUTPUT_FILE} file. Only what a program "
                f"writes to {SANDBOX_OUTPUT_FILE} is committed, never what it "
                "printed or returned."
            )

        content = filesystem.inner.path_read_text(SANDBOX_OUTPUT_FILE)
        if content == prepared.standing:
            return None, (
                f"Nothing was committed to '{prepared.output}': the program "
                "left it exactly as it was."
            )

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
