"""The skills feature: the catalog a conversation is shown, and the tool that loads a skill.

A skill is content its author wrote, a user's or a group member's as often as
the operator's, so none of it reaches the model as an instruction.  The
catalog of the skills a user sees is reported as a ``load_skill`` call without
a name, as :data:`~.memory.MEMORY_NOTE` reports the memory, when a
conversation starts, after a compaction, and whenever it changed since, which
appends to the history and never rewrites the cached prefix.  A skill's
instructions are the result of ``load_skill`` with its name, fenced as data
like every result, and its other files are read by path with the read tools.

A skill is named by its reference, its scope's prefix and its name such as
``~/csv-check``, or by its bare name while no other skill the user sees has
it (:func:`~hivegent.skills.resolve_skill`), so no scope stands in for
another's.  The host loads a skill the same way where the model did not ask
for it: one the user invokes by starting a prompt with ``/<reference>`` or
``/<name>``, its ``$ARGUMENTS`` replaced by the text after it, and after a
compaction every one the window before it had loaded, so a summary never drops
the instructions the conversation works by.  A skill that disables model
invocation is left out of the catalog and refused by ``load_skill``, and one
that is not user-invocable is never invoked by a prompt.
"""

import asyncio
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from exar.pydantic_ai import Read, ToolNote, latest_read
from exar.tools import effect
from pydantic import Field
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
    post_compaction_window,
)

from ...config import settings
from ...db.conversations import (
    HIDDEN_KEY,
    is_user_request,
    prompt_items,
    prompt_text,
)
from ...libraries import scope_author
from ...shared.skills import SKILL_FILE
from ...skills import Skill, catalog, resolve_skill
from ...tools.base import ToolFunc
from ...tools.python import is_python_script
from ..common import AgentTools, UserDeps
from .python import check_skill_script

__all__ = [
    "SKILLS_NOTE",
    "Invocation",
    "invoked_skill",
    "load_skill",
    "render_catalog",
    "skills_toolset",
]

SkillNameArg = Annotated[
    str | None,
    Field(
        description=(
            "The reference of the skill to load, such as ~/csv-check, as the "
            "catalog lists it. Omit it to list the skills you can load."
        )
    ),
]

_MAX_FILES = 100
"""How many of a skill's files a load lists, the rest counted."""

_MAX_PROBLEM_CHARS = 300

_NO_SKILLS = "No skills are available."

_ARGUMENTS = re.compile(r"\$ARGUMENTS(?![\[\w])")
"""``$ARGUMENTS`` itself, not an indexed ``$ARGUMENTS[0]`` or a longer name such as ``$ARGUMENTS_LIST``."""


def render_catalog(skills: Sequence[Skill]) -> str:
    """List *skills* by reference, path, and description, as the model reads the catalog."""
    if not skills:
        return _NO_SKILLS

    lines = [
        f"- {skill.ref} ({skill.path}, by {scope_author(skill.scope).en}): "
        f"{skill.document.manifest.description}"
        for skill in skills
        if skill.document is not None
    ]

    return "\n".join(["Skills you can load with load_skill:", *lines])


def _files(folder: Path) -> list[str]:
    """Every file below *folder* but its ``SKILL.md``, by path, hidden ones left out."""
    return sorted(
        path.relative_to(folder).as_posix()
        for path in folder.rglob("*")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(folder).parts)
        and path.name != SKILL_FILE
    )


async def _describe(skill: Skill, file: str) -> str:
    """One line on *file*, and for a script whether and how it runs."""
    if not is_python_script(file):
        return f"- {file}"

    canonical = f"{skill.path}/{file}"

    try:
        source = await asyncio.to_thread((skill.folder / file).read_text, "utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"- {file}: unreadable, {exc}"

    check = await check_skill_script(source, canonical)

    if check.problem is not None:
        return f"- {file}: reference only, it does not run: {check.problem[:_MAX_PROBLEM_CHARS]}"

    summary = "" if check.summary is None else f" {check.summary}"

    return (
        f"- {file}: runnable with run_python(script_path='{canonical}', args={{...}}) "
        f"calling {check.signature}.{summary}"
    )


async def _render_skill(skill: Skill, arguments: str | None = None) -> str:
    """The instructions of *skill*, with *arguments* for every ``$ARGUMENTS`` if given, and a listing of its files.

    The instructions are cut to fit ``llm.tool_output_max_chars`` with the
    listing, pointing at the rest in its ``SKILL.md``, since a load the host
    reports bypasses the spill that bounds a tool return.
    """
    assert skill.document is not None
    body = skill.document.body

    if arguments is not None:
        # A function, so a backslash in the arguments is no group reference.
        body = _ARGUMENTS.sub(lambda _: arguments, body)

    files = await asyncio.to_thread(_files, skill.folder)
    lines = list(
        await asyncio.gather(*(_describe(skill, file) for file in files[:_MAX_FILES]))
    )

    if len(files) > _MAX_FILES:
        lines.append(f"- and {len(files) - _MAX_FILES} more files")

    header = f"Skill {skill.ref} by {scope_author(skill.scope).en}, in {skill.path}:"
    listing = (
        "\n".join([f"Its files, by their path in {skill.path}:", *lines])
        if lines
        else "It holds no other files."
    )
    budget = max(settings.llm.tool_output_max_chars - len(header) - len(listing), 0)

    if len(body) > budget:
        body = (
            f"{body[:budget]}\n\n[The instructions are cut short here, "
            f"read the rest from {skill.path}/{SKILL_FILE}.]"
        )

    return f"{header}\n\n{body}\n\n{listing}"


@effect("read")
async def load_skill(ctx: RunContext[UserDeps], name: SkillNameArg = None) -> str:
    """Load a skill's instructions with the list of its files, or list the skills.

    Load a skill whose description matches the task before working on it.
    """
    skills = [
        skill
        for skill in await asyncio.to_thread(catalog, ctx.deps.user)
        if skill.model_invocable
    ]

    if name is None:
        return render_catalog(skills)

    skill = resolve_skill(skills, name)

    if skill is None:
        raise ModelRetry(f"No one skill is named {name!r}. {render_catalog(skills)}")

    return await _render_skill(skill)


def skills_tools(ctx: RunContext[UserDeps]) -> list[ToolFunc]:
    """The tool that loads skills, the same for every run."""
    return [load_skill]


skills_toolset = AgentTools(factory=skills_tools)
"""The skills tools."""


def _loaded(messages: Iterable[ModelMessage]) -> list[str]:
    """The skills *messages* loaded by reference, in order, each load that succeeded."""
    calls: dict[str, str] = {}
    loaded: list[str] = []

    for message in messages:
        for part in message.parts:
            if (
                isinstance(part, ToolCallPart)
                and part.tool_name == load_skill.__name__
                and isinstance(name := part.args_as_dict().get("name"), str)
            ):
                calls[part.tool_call_id] = name

            elif isinstance(part, ToolReturnPart) and part.tool_call_id in calls:
                loaded.append(calls[part.tool_call_id])

    return loaded


@dataclass(frozen=True, slots=True)
class Invocation:
    """A prompt invoking a skill: the word naming it after the slash, and the text after that."""

    word: str
    arguments: str


def invoked_skill(messages: Sequence[ModelMessage]) -> Invocation | None:
    """The skill the latest prompt of *messages* invokes by leading with ``/<word>``, if any.

    >>> from pydantic_ai.messages import ModelRequest
    >>> invoked_skill([ModelRequest(parts=[UserPromptPart("/csv-check the file")])])
    Invocation(word='csv-check', arguments='the file')
    >>> invoked_skill([ModelRequest(parts=[UserPromptPart("/~/csv-check")])])
    Invocation(word='~/csv-check', arguments='')
    >>> invoked_skill([ModelRequest(parts=[UserPromptPart("a /csv-check")])]) is None
    True
    """
    prompt = next(
        (
            message
            for message in reversed(messages)
            if is_user_request(message)
            and not any(isinstance(part, ToolReturnPart) for part in message.parts)
        ),
        None,
    )
    texts = (
        text
        for part in (prompt.parts if prompt is not None else ())
        if isinstance(part, UserPromptPart)
        for item in prompt_items(part)
        if (text := prompt_text(item)) is not None
    )
    first = next(texts, "")

    if not first.startswith("/"):
        return None

    match first[1:].split(maxsplit=1):
        case [word]:
            return Invocation(word, "")

        case [word, arguments]:
            return Invocation(word, arguments.strip())

        case _:
            return None


def _resolved(skills: Sequence[Skill], words: Iterable[str]) -> dict[str, Skill]:
    """The skills *words* name among *skills*, by reference, in order, each once."""
    return {
        skill.ref: skill
        for word in words
        if (skill := resolve_skill(skills, word)) is not None
    }


async def _skills_reads(
    ctx: RunContext[UserDeps], window: Sequence[ModelMessage]
) -> list[Read]:
    """The catalog, unless there is none to report, and the skills the host loads.

    The invoked skill is reported whether or not the window loaded it, since
    its arguments may differ, and the note skips it where they do not.
    """
    skills = await asyncio.to_thread(catalog, ctx.deps.user)
    listing = render_catalog([skill for skill in skills if skill.model_invocable])
    shown = latest_read(window, load_skill.__name__)
    previous = post_compaction_window(ctx.messages[: len(ctx.messages) - len(window)])
    loaded = _resolved(skills, _loaded(window)).keys()
    loads: dict[str, tuple[Skill, str | None]] = {
        ref: (skill, None)
        for ref, skill in _resolved(skills, _loaded(previous)).items()
        if ref not in loaded
    }

    if (invocation := invoked_skill(window)) is not None and (
        invoked := resolve_skill(
            [skill for skill in skills if skill.user_invocable], invocation.word
        )
    ) is not None:
        loads[invoked.ref] = (invoked, invocation.arguments)

    return [
        *(
            []
            if shown is None and listing == _NO_SKILLS
            else [Read(tool=load_skill.__name__, content=listing)]
        ),
        *[
            Read(
                tool=load_skill.__name__,
                content=await _render_skill(skill, arguments),
                args={"name": ref},
            )
            for ref, (skill, arguments) in loads.items()
        ],
    ]


SKILLS_NOTE = ToolNote[UserDeps](
    reads=_skills_reads, metadata={HIDDEN_KEY: True}, id="skills-note"
)
"""Reports the catalog and every skill the host loads as hidden ``load_skill`` calls.

The catalog is reported where the window shows another catalog or none,
unless there are no skills to report at all.  A skill is loaded where the
window has not loaded it yet but the window before the latest compaction
loaded it, and where the latest prompt invokes it with arguments the window
has not loaded it with.
The user never asked for any of it, so the transcript leaves it out
(:data:`HIDDEN_KEY`).
"""
