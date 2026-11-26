"""API models of skills."""

from pydantic import BaseModel, Field

__all__ = [
    "SkillInfo",
    "SkillInstalledResponse",
    "SkillScriptInfo",
]


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
