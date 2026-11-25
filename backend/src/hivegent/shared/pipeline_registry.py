"""The contract every conversion and chunking pipeline registers by, free of app dependencies.

The built-in pipelines and every plugin's register a `Pipeline` under a string id,
which `hivegent.pipelines` merges into the registries an operator configures.
A pipeline is async, the synchronous ones of `exar.ingest` run `exar.ingest.Offloaded` in the worker pool of the host.
"""

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from importlib.util import find_spec
from typing import Any

from exar.ingest import AsyncChunker, AsyncParser, Host

from .l10n import Localized

__all__ = ["ChunkerPipeline", "ConverterPipeline", "Pipeline"]


@cache
def _installed(dependencies: tuple[str, ...], executables: tuple[str, ...]) -> bool:
    return all(find_spec(name) is not None for name in dependencies) and all(
        shutil.which(name) is not None for name in executables
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Pipeline[T]:
    """A pipeline as an operator configures it and a user picks it.

    Args:
        label: The name a user picks it by.
        description: What it is good at, in one line.
        build: Builds it from the operator's options and the host facts,
            raising `ValueError` or `ValidationError` for options it refuses.
        dependencies: Top-level modules it needs installed.
        executables: Executables it needs on the path.
        requires: Condition on the host beyond the installed requirements.
    """

    label: Localized[str]
    description: Localized[str]
    build: Callable[[Mapping[str, Any], Host], T]
    dependencies: tuple[str, ...] = ()
    executables: tuple[str, ...] = ()
    requires: Callable[[], bool] | None = None

    def available(self) -> bool:
        """Whether the requirements are installed and the condition holds."""
        return _installed(self.dependencies, self.executables) and (
            self.requires is None or self.requires()
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ConverterPipeline(Pipeline[AsyncParser]):
    """A converter with the extensions it reads, known without importing it.

    Args:
        extensions: The extensions it accepts, any file for None.
            A hard capability, `get_converter` refuses a file outside it.
        auto_extensions: The extensions AUTO prefers it for,
            its claimants tried in registration order.
            Kept apart since plain text accepts every file but should only win AUTO for raw text formats.
    """

    extensions: frozenset[str] | None = None
    auto_extensions: frozenset[str] = frozenset()


type ChunkerPipeline = Pipeline[AsyncChunker]
"""A chunker as an operator configures it."""
