"""Interface languages and the text catalogs keyed by them.

Every text a user reads, and every instruction steering the language the model
answers in, is a :class:`Localized` value holding one entry per
:data:`Language`, so a missing translation is a type error rather than a silent
English fallback.  Tool schemas, tool results and retries stay English: models
call tools most reliably from English schemas, and the explicit language pin in
the instructions decides the answer language.  Instructions are selected with
the run's explicit language, while messages raised deep inside a request (HTTP
error details) read the ambient :func:`current_language`, which the server sets
from the ``Accept-Language`` header of every request and every agent tool call
pins to the default.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal, assert_never

__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "Language",
    "Localized",
    "current_language",
    "negotiate_language",
    "use_language",
]

type Language = Literal["en", "de"]
"""An interface language, named by its ISO 639-1 code."""

LANGUAGES: tuple[Language, ...] = ("en", "de")
"""Every supported language, the default first."""

DEFAULT_LANGUAGE: Language = "en"
"""The language of requests that state none we support, and of runs without a user."""

_current: ContextVar[Language] = ContextVar("language", default=DEFAULT_LANGUAGE)


@dataclass(slots=True, frozen=True)
class Localized[T]:
    """One value per interface language.

    Fixed text is a constant, and text with parameters is a function returning
    one, so its parameters are declared and type checked once for every language:

    Examples:
        >>> GREETING = Localized(en="Hello", de="Hallo")
        >>> GREETING["de"]
        'Hallo'
        >>> def files(n: int) -> Localized[str]:
        ...     return Localized(en=f"{n} files", de=f"{n} Dateien")
        >>> files(3)["de"]
        '3 Dateien'
    """

    en: T
    de: T

    def __getitem__(self, language: Language) -> T:
        """The value for *language*."""
        match language:
            case "en":
                return self.en
            case "de":
                return self.de
            case _:
                # Reached at runtime only, e.g. by iterating, which probes 0, 1, ...
                assert_never(language)

    @property
    def current(self) -> T:
        """The value for the language of the request being served."""
        return self[current_language()]


def current_language() -> Language:
    """The language of the request being served, the default outside one."""
    return _current.get()


@contextmanager
def use_language(language: Language) -> Iterator[None]:
    """Serve everything inside the block in *language*."""
    token = _current.set(language)

    try:
        yield
    finally:
        _current.reset(token)


def negotiate_language(accept_language: str | None) -> Language:
    """Pick the supported language an ``Accept-Language`` header prefers most.

    Regional variants match their base language, and a header naming no
    supported language yields the default.

    Examples:
        >>> negotiate_language("de-AT,de;q=0.9,en;q=0.8")
        'de'
        >>> negotiate_language("fr, en;q=0.5, de;q=0.7")
        'de'
        >>> negotiate_language(None)
        'en'
    """
    ranked: list[tuple[float, Language]] = []

    for entry in (accept_language or "").split(","):
        tag, _, params = entry.strip().partition(";")
        language: Language

        match tag.split("-")[0].strip().lower():
            case "en":
                language = "en"
            case "de":
                language = "de"
            case _:
                continue

        quality = params.strip().removeprefix("q=")

        try:
            ranked.append((float(quality) if quality else 1.0, language))
        except ValueError:
            continue

    return max(ranked, key=lambda item: item[0])[1] if ranked else DEFAULT_LANGUAGE
