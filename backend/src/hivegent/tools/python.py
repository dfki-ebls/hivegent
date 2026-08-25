"""Run small Python programs in the Monty sandbox."""

import reprlib
from dataclasses import dataclass, field
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
    ResourceLimits,
)

from ..humanize import pluralize
from .base import AsyncTool, ToolOutput, ToolRetry
from .formatting import cap_lines, hint_suffix, truncate_line

__all__ = ["CodeArg", "PythonResult", "RunPythonTool"]

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
    str,
    Field(
        description=(
            "The Python program to run. Its trailing expression is the value "
            "returned to you, and whatever it prints comes back alongside."
        ),
    ),
]


def _default_limits() -> ResourceLimits:
    """The budget one program runs under when no caller sets one."""
    return {"max_duration_secs": 5.0, "max_memory": 256_000_000}


def _diagnostic(exc: MontyError, printed: str) -> str:
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
    printed = printed.rstrip("\n")

    if not printed:
        return detail

    return f"Printed before the failure:\n{printed}\n\n{detail}"


@dataclass(slots=True, frozen=True)
class PythonResult:
    """What one program produced: its value and anything it printed."""

    result: str | None = None
    """The trailing expression's value, or ``None`` when there was none."""

    stdout: str = ""
    truncated: bool = False
    """Whether printed output was cut to fit the budget."""


@dataclass(slots=True, frozen=True)
class RunPythonTool(AsyncTool[PythonResult]):
    """Run a Python program in a sandbox that reaches nothing outside itself.

    Each call takes a fresh session out of the pool, so one program never sees
    another's variables and never inherits what another spent of the budget:
    :attr:`limits` caps execution time cumulatively per session, which would
    otherwise leave a session that once looped failing every later call.
    """

    pool: AsyncMonty
    limits: ResourceLimits = field(default_factory=_default_limits)
    max_output_chars: int = 20_000
    max_line_chars: int = 2_000

    @override
    async def __call__(self, code: CodeArg) -> ToolOutput[PythonResult]:
        """Run a short Python program and return what it printed and evaluated.

        Reach for it whenever an answer turns on arithmetic, dates, sorting, or
        counting, rather than working the result out in your head.  The program
        runs in an isolated interpreter with no network, no filesystem, and no
        access to the user's documents, so every input it needs must appear as
        a literal in the code you send.  Only ``asyncio``, ``collections``,
        ``dataclasses``, ``datetime``, ``functools``, ``itertools``, ``json``,
        ``math``, ``os``, ``pathlib``, ``re``, ``sys``, ``typing``, and
        ``unicodedata`` can be imported, there is no numpy or pandas, and
        classes cannot inherit.  End the program with the expression whose
        value you want back, and print anything else worth seeing.
        """
        printed = CollectString()

        async with self.pool.checkout(
            # Quoted in the sandbox's own error messages.
            script_name="script.py",
            limits=self.limits,
        ) as session:
            try:
                value = await session.feed_run(code, print_callback=printed)

            # The pool itself is gone, which no rewrite of the program fixes.
            except (MontyShutdown, MontyDisconnectError):
                raise

            except MontyError as exc:
                raise ToolRetry(_diagnostic(exc, printed.output)) from exc

        return self._output(value, printed.output)

    def _output(self, value: object, printed: str) -> ToolOutput[PythonResult]:
        """Budget what the program produced and render it for the model.

        The value is what the call was for, so it keeps the whole output budget
        to itself and printed output is bounded separately.
        """
        lines = (
            truncate_line(line, self.max_line_chars) for line in printed.splitlines()
        )
        stdout, dropped = cap_lines(lines, self.max_output_chars)
        result = (
            None
            if value is None
            else truncate_line(_VALUE_REPR.repr(value), self.max_output_chars)
        )

        parts = [stdout] if stdout else []
        if result is not None:
            parts.append(f"Result: {result}")

        hints = (
            [f"{dropped} more printed {pluralize(dropped, 'line')}"] if dropped else []
        )
        body = "\n".join(parts) or "The program printed nothing and returned no value."

        return ToolOutput(
            data=PythonResult(result=result, stdout=stdout, truncated=bool(dropped)),
            formatted=body + hint_suffix(hints),
        )
