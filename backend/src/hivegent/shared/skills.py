"""The Agent Skills format (agentskills.io): a folder holding a ``SKILL.md``.

A skill's ``SKILL.md`` opens with YAML frontmatter, which names and describes
it, and continues with its instructions in Markdown, while the rest of the
folder holds what they refer to: ``scripts/``, ``references/``, and
``assets/`` by convention.  This module parses and validates the format,
where skills live and who sees them is the app's.

The frontmatter keeps to the specification's fields and to two of Claude
Code's: ``disable-model-invocation``, which leaves a skill to the user and the
assistants that preload it, and ``user-invocable``, which leaves it to the
model.  A key it does not know, such as one another client reads, is reported
as a warning rather than refused, as the client guide advises, and
``allowed-tools`` is accepted but never read, since nothing a skill declares
widens what a run may do.  Every value is read as written, without YAML's
implicit types, so ``version: 1.0`` stays the text ``1.0``, and a key given
twice is refused rather than one of its values silently winning.
"""

import re
from collections import Counter
from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

__all__ = [
    "SKILL_FILE",
    "InvalidSkill",
    "SkillDocument",
    "SkillManifest",
    "parse_skill",
    "valid_skill_name",
]

SKILL_FILE = "SKILL.md"
"""The file every skill folder holds, named as the specification spells it."""

_NAME = r"[a-z0-9]+(?:-[a-z0-9]+)*"
"""Lowercase letters, digits, and single inner hyphens."""

_FIELDS = frozenset(
    {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
        "disable-model-invocation",
        "user-invocable",
    }
)

_DELIMITER = "---"


class InvalidSkill(ValueError):
    """A skill that does not keep to the format, the message saying where."""


def _flag(value: object) -> object:
    """Read *value* as a flag, which only ``true`` and ``false`` spell, so ``yes`` fails rather than guesses.

    >>> _flag("True"), _flag("false")
    (True, False)
    """
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"

    raise ValueError(f"must be true or false, not {value!r}")


type _Flag = Annotated[bool, BeforeValidator(_flag)]


class SkillManifest(BaseModel):
    """The frontmatter of a ``SKILL.md``, held to the specification."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: Annotated[str, StringConstraints(max_length=64, pattern=f"^{_NAME}$")]
    """Equal to the name of the folder holding it."""

    description: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    """What the skill does and when to use it, which a catalog lists."""

    license: str | None = None
    compatibility: (
        Annotated[str, StringConstraints(min_length=1, max_length=500)] | None
    ) = None
    metadata: dict[str, str] = Field(default_factory=dict)

    disable_model_invocation: _Flag = Field(
        default=False, alias="disable-model-invocation"
    )
    """Whether only the user and the assistants that preload it load it, never the model."""

    user_invocable: _Flag = Field(default=True, alias="user-invocable")
    """Whether a user invokes it by ``/<reference>``, else only the model and assistants load it."""


@dataclass(frozen=True, slots=True)
class SkillDocument:
    """A parsed ``SKILL.md``: its manifest, its instructions, and what was off.

    Attributes:
        manifest: The validated frontmatter.
        body: The Markdown following the frontmatter.
        warnings: The frontmatter keys the specification does not name.
    """

    manifest: SkillManifest
    body: str
    warnings: tuple[str, ...] = ()


def valid_skill_name(name: str) -> bool:
    """Whether *name* is a skill name the specification allows.

    >>> valid_skill_name("pdf-tools"), valid_skill_name("PDF"), valid_skill_name("a--b")
    (True, False, False)
    """
    return len(name) <= 64 and re.fullmatch(_NAME, name) is not None


def _frontmatter(text: str) -> tuple[str, str]:
    """Split *text* into its frontmatter and its body.

    >>> _frontmatter("---\\nname: a\\n---\\nBody")
    ('name: a', 'Body')

    Raises:
        InvalidSkill: When *text* opens with no frontmatter or never closes it.
    """
    lines = text.removeprefix("\N{BYTE ORDER MARK}").replace("\r\n", "\n").split("\n")

    if lines[0].rstrip() != _DELIMITER:
        raise InvalidSkill(f"{SKILL_FILE} must open with YAML frontmatter.")

    end = next(
        (i for i, line in enumerate(lines[1:], 1) if line.rstrip() == _DELIMITER),
        None,
    )

    if end is None:
        raise InvalidSkill(f"The frontmatter of {SKILL_FILE} is never closed.")

    return "\n".join(lines[1:end]), "\n".join(lines[end + 1 :]).strip()


class _Loader(yaml.BaseLoader):
    """Reads every scalar as the text it spells and refuses a key a mapping repeats."""

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Hashable, Any]:
        keys = Counter(
            key.value for key, _ in node.value if isinstance(key, yaml.ScalarNode)
        )

        if repeated := sorted(key for key, count in keys.items() if count > 1):
            raise InvalidSkill(
                f"The frontmatter repeats the key {', '.join(map(repr, repeated))}."
            )

        return super().construct_mapping(node, deep)


def _mapping(frontmatter: str) -> Mapping[str, Any]:
    """The frontmatter as the mapping it must be, every value read as written.

    >>> _mapping("name: a\\nversion: 1.0\\nlive: yes")
    {'name': 'a', 'version': '1.0', 'live': 'yes'}

    Raises:
        InvalidSkill: When it is no YAML mapping, or repeats a key.
    """
    try:
        data: object = yaml.load(frontmatter, Loader=_Loader)
    except yaml.YAMLError as exc:
        raise InvalidSkill(f"The frontmatter is no valid YAML: {exc}") from None

    if not isinstance(data, dict) or not data:
        raise InvalidSkill("The frontmatter is no YAML mapping.")

    return {str(key): value for key, value in data.items()}


def parse_skill(text: str, folder: str | None = None) -> SkillDocument:
    """Parse the content of a ``SKILL.md``, held in the folder named *folder* if known.

    >>> parse_skill("---\\nname: csv-check\\ndescription: Checks CSVs.\\n---\\nRun it.").body
    'Run it.'

    Raises:
        InvalidSkill: When it breaks the format, or names another folder than *folder*.
    """
    frontmatter, body = _frontmatter(text)
    data = _mapping(frontmatter)

    try:
        manifest = SkillManifest.model_validate(data)
    except ValidationError as exc:
        reasons = "; ".join(
            f"{'.'.join(map(str, error['loc'])) or 'frontmatter'}: {error['msg']}"
            for error in exc.errors(include_url=False, include_input=False)
        )

        raise InvalidSkill(f"Invalid frontmatter, {reasons}.") from None

    if folder is not None and manifest.name != folder:
        raise InvalidSkill(
            f"The skill is named {manifest.name!r}, but its folder {folder!r}."
        )

    if not body:
        raise InvalidSkill(f"{SKILL_FILE} holds no instructions after its frontmatter.")

    warnings = tuple(
        f"The frontmatter key {key!r} is no field of the specification and is ignored."
        for key in sorted(set(data) - _FIELDS)
    )

    return SkillDocument(manifest, body, warnings)
