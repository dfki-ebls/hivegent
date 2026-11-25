"""API models of assistants and skills."""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from ..libraries import split_library_ref
from ..shared.l10n import Localized
from ..shared.skills import valid_skill_name
from .common import Mode
from .filters import FilterEntry

__all__ = [
    "AssistantInfo",
    "AssistantName",
    "AssistantSpec",
    "LibraryRef",
    "SkillInfo",
    "SkillInstalledResponse",
    "SkillScriptInfo",
]


_INVALID_ASSISTANT_NAME = Localized(
    en="An assistant name holds lowercase letters, digits, and single inner hyphens",
    de="Ein Assistentenname besteht aus Kleinbuchstaben, Ziffern und einzelnen inneren Bindestrichen",
)


def _check_assistant_name(name: str) -> str:
    """*name*, refused unless it is kebab-case like a skill's."""
    if not valid_skill_name(name):
        raise ValueError(_INVALID_ASSISTANT_NAME.current)

    return name


type AssistantName = Annotated[str, AfterValidator(_check_assistant_name)]
"""An assistant's name, kebab-case like a skill's, which is also its file's."""


def _check_library_ref(ref: str) -> str:
    """*ref*, refused unless it names a scope and a name."""
    _ = split_library_ref(ref)

    return ref


type LibraryRef = Annotated[str, AfterValidator(_check_library_ref)]
"""A skill or an assistant by its scope's prefix and its name, such as ``~/legal`` or ``/srv/legal``."""


class AssistantSpec(BaseModel):
    """What an assistant is, as its file holds it and a client writes it.

    Everything but its instructions only narrows a run: it withholds tools,
    caps the mode, adds to the document selection, and confines the documents
    a run reaches.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str = Field(default="", max_length=1024)
    instructions: str = Field(min_length=1, max_length=20_000)
    skills: list[LibraryRef] = Field(
        default_factory=list,
        description="The references of the skills it loads when a window starts",
    )
    disabled_tools: list[str] = Field(
        default_factory=list, description="The tools it withholds"
    )
    included_documents: list[FilterEntry] = Field(default_factory=list)
    excluded_documents: list[FilterEntry] = Field(default_factory=list)
    allowed_documents: list[FilterEntry] = Field(
        default_factory=list,
        description="The only paths a run with it reaches, none for no restriction",
    )
    max_mode: Mode = Field(
        default="write", description="The mode a run with it may at most take"
    )


class AssistantInfo(AssistantSpec):
    """An assistant a user sees."""

    name: str
    ref: str = Field(description="The reference a chat names it by, such as ~/name")
    scope: str = Field(
        description="The prefix of the scope owning it: ~, @<group>, or /srv"
    )
    writable: bool = Field(description="Whether the user may replace or delete it")


class SkillInfo(BaseModel):
    """A skill a user sees, valid or with the reason it is not."""

    name: str = Field(description="The skill's name, its folder's")
    ref: str = Field(
        description="The reference it is loaded and invoked by, such as ~/name"
    )
    path: str = Field(
        description="The canonical path of its folder, such as /skills/~/name"
    )
    scope: str = Field(
        description="The prefix of the scope owning it: ~, @<group>, or /srv"
    )
    description: str | None = Field(description="What it does, none when it is invalid")
    problem: str | None = Field(description="Why it is invalid, none when it is valid")
    warnings: list[str] = Field(
        description="What its frontmatter holds beyond the format"
    )
    writable: bool = Field(description="Whether the user may replace or delete it")
    model_invocable: bool = Field(
        description="Whether the model may load it, false for disable-model-invocation"
    )
    user_invocable: bool = Field(
        description="Whether a prompt may invoke it by /<ref>, false for user-invocable: false"
    )


class SkillScriptInfo(BaseModel):
    """Whether a script of a skill runs by the main convention."""

    path: str = Field(description="Its path in the skill folder")
    signature: str | None = Field(description="Its main function as defined")
    problem: str | None = Field(description="Why it does not run, none when it does")


class SkillInstalledResponse(BaseModel):
    """A skill just installed, with the check of every script it holds."""

    skill: SkillInfo
    scripts: list[SkillScriptInfo]
