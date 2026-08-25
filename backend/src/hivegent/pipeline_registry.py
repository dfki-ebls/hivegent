"""Typed lazy loading shared by processing pipeline registries."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from importlib.util import find_spec
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel

__all__ = [
    "PipelineConfigInfo",
    "PipelineImplementation",
    "PipelineRegistration",
]


class NamedPipeline(Protocol):
    """Pipeline implementation carrying its persisted registry name."""

    name: ClassVar[str]


@dataclass(slots=True, frozen=True)
class PipelineImplementation[T: NamedPipeline]:
    """A lazily imported implementation and its optional config model."""

    cls: type[T]
    config: type[BaseModel] | None = None


@dataclass(slots=True, frozen=True)
class PipelineConfigInfo:
    """Configuration metadata loaded for one selected pipeline."""

    schema: dict[str, Any]
    defaults: dict[str, Any]


@cache
def _dependencies_available(dependencies: tuple[str, ...]) -> bool:
    """Return whether registered top-level dependency modules are installed."""
    return all(find_spec(dependency) is not None for dependency in dependencies)


@dataclass(slots=True, frozen=True, kw_only=True)
class PipelineRegistration[T: NamedPipeline]:
    """Dependency-free metadata and a typed lazy pipeline loader."""

    loader: Callable[[], PipelineImplementation[T]]
    label: str
    description: str
    dependencies: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        """Return whether the pipeline's declared dependencies are installed."""
        return _dependencies_available(self.dependencies)

    def load(self, expected_name: str) -> PipelineImplementation[T]:
        """Load the registered implementation and validate its persisted name.

        The name is the registry key that :mod:`hivegent.workspace.prepare` maps
        back to a pipeline, so a mismatch is caught here rather than mid-run.
        """
        implementation = self.loader()
        if implementation.cls.name != expected_name:
            raise TypeError(
                f"{implementation.cls.__name__}.name is "
                f"{implementation.cls.name!r}, expected {expected_name!r}"
            )

        return implementation

    def config_info(self, expected_name: str) -> PipelineConfigInfo:
        """Load the implementation and describe its configuration model."""
        model = self.load(expected_name).config
        return PipelineConfigInfo(
            schema=model.model_json_schema() if model else {},
            defaults=model().model_dump() if model else {},
        )
