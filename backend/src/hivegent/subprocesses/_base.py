"""Shared typed async subprocess runner."""

import asyncio
import json
from collections.abc import Container, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["SubprocessError", "SubprocessResult", "run"]


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
        """Parse stdout as JSON and validate against a type.

        Uses ``json.loads``; the *type_* hint is for documentation —
        runtime validation is the caller's responsibility.
        """
        return json.loads(self.stdout)

    def stdout_ndjson(self) -> Iterator[Any]:
        """Parse stdout as newline-delimited JSON (one object per line)."""
        for line in self.stdout_text.splitlines():
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
    check: bool = True,
    allowed_returncodes: Container[int] = (),
) -> SubprocessResult:
    """Run a CLI command asynchronously and capture its output.

    Args:
        args: Command and arguments.
        stdin: Bytes to pipe to stdin.
        cwd: Working directory.
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
    )
    stdout, stderr = await proc.communicate(input=stdin)
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
