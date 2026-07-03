"""Registry of recovery fallbacks for documents the AUTO converter mishandles.

Docling is the default AUTO converter for binary formats, but some documents
defeat it in ways worth recovering rather than dropping to a plain-text stub:

- Office files whose embedded parts docling loads too strictly (it *raises*);
  LibreOffice opens them leniently and we recover their text.
- Legacy PDFs with no ToUnicode CMap, where docling's glyph-id backend *succeeds*
  but emits raw glyph names (``/G56/G6F``...); poppler reconstructs the text from
  the glyph-name convention.

Each :class:`Fallback` declares the suffixes it handles, a ``trigger`` predicate
over the primary converter's outcome (a :class:`ConversionResult` on success or
the :class:`Exception` it raised), and an async ``recover`` that returns markdown
or ``None``.  :func:`recover_conversion` consults the registry in order and
returns the first successful recovery, tagged with the pipeline to record as
provenance.  Extending to a new format is one appended entry — the consumer code
(``workspace.prepare``) never changes.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from . import ConversionPipeline
from .base import ConversionResult
from .libreoffice import OFFICE_FALLBACK_SUFFIXES, recover_office_markdown
from .poppler import is_pdf_text_garbled, recover_pdf_markdown

__all__ = ["Fallback", "Recovery", "recover_conversion"]


# The outcome a fallback reacts to: the primary converter's result, or the error
# it raised.  A single union lets one predicate cover both failure and degraded
# success without the consumer branching on which happened.
type ConversionOutcome = ConversionResult | Exception


@dataclass(frozen=True, slots=True)
class Fallback:
    """A recovery path for documents the primary AUTO converter mishandles."""

    pipeline: ConversionPipeline
    """Provenance recorded in ``conversion_pipeline_used`` for this fallback's recoveries."""

    suffixes: frozenset[str]
    """Lowercase file extensions (``.pdf``, ``.docx``, ...) this fallback handles."""

    trigger: Callable[[ConversionOutcome], bool]
    """Whether the primary outcome warrants this recovery."""

    recover: Callable[[Path], Awaitable[str | None]]
    """Attempt recovery from the source file, returning markdown or ``None``."""


@dataclass(frozen=True, slots=True)
class Recovery:
    """A fallback's recovered markdown and the pipeline to record for it."""

    markdown: str
    pipeline: ConversionPipeline


def _garbled_pdf(outcome: ConversionOutcome) -> bool:
    """Whether the converter succeeded but produced glyph-name gibberish."""
    return isinstance(outcome, ConversionResult) and is_pdf_text_garbled(
        outcome.markdown
    )


# Order matters only when two fallbacks could claim the same suffix; today's two
# are disjoint (Office suffixes vs. ``.pdf``), so the first match always applies.
_FALLBACKS: tuple[Fallback, ...] = (
    Fallback(
        pipeline=ConversionPipeline.LIBREOFFICE,
        suffixes=OFFICE_FALLBACK_SUFFIXES,
        trigger=lambda outcome: isinstance(outcome, Exception),
        recover=recover_office_markdown,
    ),
    Fallback(
        pipeline=ConversionPipeline.POPPLER,
        suffixes=frozenset({".pdf"}),
        trigger=_garbled_pdf,
        recover=recover_pdf_markdown,
    ),
)


async def recover_conversion(
    source: Path,
    suffix: str,
    outcome: ConversionOutcome,
    *,
    registry: tuple[Fallback, ...] = _FALLBACKS,
) -> Recovery | None:
    """Return the first fallback recovery for a failed or degraded conversion.

    Consults *registry* (the module registry by default) in order for a fallback
    that handles *suffix* and whose trigger fires on *outcome*.  Returns the
    recovered markdown tagged with its provenance pipeline, or ``None`` when none
    applies or recovery is impossible (the caller then keeps a degraded result,
    or re-raises a failure to drop to the plain-text/stub path).
    """
    for fallback in registry:
        if suffix not in fallback.suffixes or not fallback.trigger(outcome):
            continue
        recovered = await fallback.recover(source)
        if recovered is not None:
            return Recovery(markdown=recovered, pipeline=fallback.pipeline)

    return None
