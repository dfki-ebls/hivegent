"""Shared typed async subprocess runner."""

import asyncio
import json
from collections.abc import AsyncGenerator, Container, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from ..concurrency import shield_to_completion

__all__ = ["SubprocessError", "SubprocessResult", "run", "stream_lines"]

_LINE_LIMIT = 1 << 30
"""The longest line :func:`stream_lines` reads, far past any document's, since one line is one record."""


@dataclass(slots=True, frozen=True)
class SubprocessResult:
    """Captured output of a completed subprocess."""

    stdout: bytes
    stderr: bytes
    returncode: int

    @property
    def stdout_text(self) -> str:
        """Decode stdout as UTF-8."""
        return self.stdout.decode("utf-8", errors="replace")

    @property
    def stderr_text(self) -> str:
        """Decode stderr as UTF-8."""
        return self.stderr.decode("utf-8", errors="replace")

    def stdout_json[T](self, type_: type[T]) -> T:
        """Parse stdout as JSON and validate it against *type_*."""
        return TypeAdapter(type_).validate_json(self.stdout)

    def stdout_ndjson(self) -> Iterator[JsonValue]:
        """Parse stdout as newline-delimited JSON (one object per line).

        Split on the raw bytes rather than :attr:`stdout_text`: a large result
        set would otherwise be copied twice at full size, and ``str`` splits on
        more separators than JSON treats as line breaks.
        """
        for line in self.stdout.splitlines():
            if line.strip():
                yield json.loads(line)


class SubprocessError(Exception):
    """Raised when a subprocess exits with an unexpected return code."""

    def __init__(
        self,
        result: SubprocessResult,
    ) -> None:
        self.result = result
        super().__init__(
            f"Subprocess exited with code {result.returncode}\n"
            f"stderr: {result.stderr_text}"
        )


async def run(
    args: Sequence[str | Path],
    *,
    stdin: bytes | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    allowed_returncodes: Container[int] = (),
) -> SubprocessResult:
    """Run a CLI command asynchronously and capture its output.

    Args:
        args: Command and arguments.
        stdin: Bytes to pipe to stdin.
        cwd: Working directory.
        env: Environment of the child, the server's own when ``None``.
        check: Raise SubprocessError on unexpected return code.
        allowed_returncodes: Additional return codes besides 0 that are
            acceptable (e.g., rg returns 1 for "no matches").
    """
    proc = await asyncio.create_subprocess_exec(
        *[str(a) for a in args],
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )

    try:
        stdout, stderr = await proc.communicate(input=stdin)
    except BaseException:
        # Never leave the child running when the await is interrupted — a
        # cancelled background job (or any error) must reclaim the process
        # instead of letting a long-running CLI keep pegging the CPU.  Reap it
        # to completion (surviving a further cancel) so it cannot linger as a
        # zombie with its pipes still open.
        if proc.returncode is None:
            proc.kill()
            await shield_to_completion(proc.wait())

        raise
    result = SubprocessResult(
        stdout=stdout,
        stderr=stderr,
        returncode=proc.returncode or 0,
    )
    if (
        check
        and result.returncode != 0
        and result.returncode not in allowed_returncodes
    ):
        raise SubprocessError(result)
    return result


async def stream_lines(
    args: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    allowed_returncodes: Container[int] = (),
) -> AsyncGenerator[bytes]:
    """Yield the lines a command writes to stdout as it writes them.

    For a command whose output a caller may stop reading early: closing the
    generator kills the command, so it stops once the caller has enough
    rather than producing the rest into a pipe nobody reads.  stderr is
    drained alongside, so a command reporting many errors never blocks on it.

    Raises:
        SubprocessError: Once stdout is read to its end, if the command exited
            with a code other than 0 or one of *allowed_returncodes*.
    """
    proc = await asyncio.create_subprocess_exec(
        *[str(a) for a in args],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        limit=_LINE_LIMIT,
    )
    assert proc.stdout is not None and proc.stderr is not None
    stderr = asyncio.ensure_future(proc.stderr.read())

    try:
        async for line in proc.stdout:
            yield line

        returncode = await proc.wait()

        if returncode != 0 and returncode not in allowed_returncodes:
            raise SubprocessError(SubprocessResult(b"", await stderr, returncode))

    finally:
        if proc.returncode is None:
            proc.kill()
            await shield_to_completion(proc.wait())

        # Read only for the error above, so an early close drops it.
        _ = stderr.cancel()
