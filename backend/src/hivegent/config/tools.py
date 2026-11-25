"""Settings of the agent tools, the sandbox, and the per-conversation state."""

from pathlib import Path
from typing import Literal

from pydantic import Field, PositiveInt

from .common import HttpOperatorUrl, Section

__all__ = [
    "AssistantSettings",
    "ConversationSettings",
    "SandboxSettings",
    "SkillSettings",
    "TmpSettings",
    "ToolsSettings",
]


class ToolsSettings(Section):
    """Operator control over which of the agent's tools reach the model.

    ``search_url`` is the metasearch service ``web_search`` queries, of the
    kind ``search_backend`` names, and the tool is only registered while it is
    set, as ``web_fetch`` is only while ``security.web_urls`` allows a host.
    It is an operator endpoint, reached with the trusted client, so it may
    name a host in ``network.unix_sockets``.

    ``budgets`` caps how many times a run may call a tool, by its name, such as
    an expensive or rate-limited one, and a call past it is answered with a
    refusal the model reads rather than run.  It is checked like ``disabled``.

    ``disabled`` names individual tools to withhold, sharing both the word and
    the effect with a chat request's ``disabled_tools``: the schema
    never reaches the model, and a feature whose every tool is named loses its
    instruction block too.  It is what is left of ``defer_loading``, which hid
    a tool from the initial request and offered it back through tool search.
    Deferral bought nothing here. The only model class this application
    builds is ``OpenAIChatModel``, which renders no wire-level deferral, so a
    deferred tool was simply absent and a model handed a pointer to one worked
    around it rather than searching.  A schema is either worth its tokens on
    every request or it should not be registered, and which of the two a given
    deployment thinks it is, is an operator's call.

    Nothing is withheld by default, and a withheld tool is withheld from the
    subagents too, so naming the conversation tools also switches off the
    ``conversations`` subagent.  Naming a tool the agent does not have is a startup error
    (:func:`~hivegent.agents.check_tool_settings`), so a typo fails loudly
    instead of silently excluding nothing.

    ``sandbox_only`` moves a tool out of the model's tool list and leaves it
    callable from inside ``run_python``, which is the third answer to the same
    question the other two ask: a tool the model has, a tool it does not, and a
    tool a program has on its behalf.  It buys back the schema a tool costs on
    every request, and it costs the model the ability to make the call on its
    own.  Only a tool the sandbox can actually be given may be named
    (``agents.tools.INJECTABLE_TOOL_NAMES``), since anything else would
    withhold a tool and hand it to nobody, and ``disabled`` wins over it: a
    tool disabled outright reaches neither surface.

    What suits a tool to it is a schema much larger than its declaration and a
    result that is nearly always an input to further work rather than an answer
    on its own.

    Two things rule one out even where that arithmetic favours moving it.  The
    first is a consumer of the tool part: a citation source is registered off a
    tool result, with the line range and character offsets the document view
    highlights from, and a call made inside a program emits no tool part for
    that to read.  The second is a pointer, since a refusal or a hint that
    names a tool has to name one the model can invoke; following a pointer to a
    name carrying no schema leaves it one indirection short of the call, which
    is the position ``defer_loading`` was removed for leaving it in.
    """

    search_backend: Literal["searxng", "openserp"] = "searxng"
    search_url: HttpOperatorUrl | None = None
    disabled: list[str] = []
    sandbox_only: list[str] = []
    budgets: dict[str, PositiveInt] = {}


class SandboxSettings(Section):
    """Worker pool and resource budget for the model's Python sandbox.

    The pool settings size the `monty` worker subprocesses the ``run_python``
    tool checks sessions out of: ``min_processes`` workers stay warm so a
    snippet never pays a process start, and ``max_processes`` (the CPU count by
    default) caps how many snippets run at once, with further checkouts waiting
    up to ``checkout_timeout_seconds`` for a worker to come free.  These
    processes are additive to the conversion pools (`hivegent.pipelines.POOL`
    and `hivegent.media.ISOLATED`), which are sized on their own.

    The budgets bound one snippet.  ``max_duration_seconds`` counts only time
    the interpreter spends executing bytecode, measured on
    ``execution_clock``, and separately caps the time it may sleep, which Monty
    keeps off that clock.  The ``cpu`` clock keeps a budget fair under server
    load but works on Linux alone and needs a finite
    ``request_timeout_seconds``, so other systems set ``wall``.
    ``max_memory_bytes`` counts what the worker's allocator hands out, both
    enforced inside the sandbox and reported to the model as a plain ``TimeoutError`` or ``MemoryError`` it can
    correct its code from.  ``request_timeout_seconds`` is the parent-side
    backstop behind them: a worker that blows past it is killed and replaced,
    so it should sit comfortably above ``max_duration_seconds``.

    ``type_check`` checks a program before running it, so a field the injected
    tool stub does not declare, or a missing ``await`` on one of those tools,
    costs a diagnostic naming it rather than a run that reads documents and
    then fails.  It is a whole type checker and not a check of that stub, so it
    also rejects unsound code that would have run (``Path(os.getenv("TMPDIR"))``
    for its ``str | None``, where the program should name ``/tmp`` outright),
    and turning it off leaves the stub in the prompt as guidance.

    The ``max_changeset_*`` limits bound what one program may stage as
    workspace changes: the operations, one for each path it creates, changes,
    moves, or deletes, how many of those are deletions, and the characters it
    writes in total.  A program that passes one is told so where it stands,
    as an exception it can handle.

    A program may read or write one document as long as
    :attr:`LimitsSettings.max_file_size_bytes` in characters, so it can open
    every document the workspace accepts.  The document is decoded in the
    server process outside ``max_memory_bytes``, which must leave room for the
    program to hold one and work on it, and ``max_changeset_chars`` sits above it.

    ``max_host_calls`` caps how many calls one program makes to the functions
    it is handed, the injected tools and ``complete`` alike, so a runaway loop
    is refused at the call that passes it rather than spending the turn.
    ``max_suspensions`` is Monty's cap on everything a program waits on the
    host for, each file operation and name lookup as well as each host call,
    which counts about three, so it sits well above ``max_host_calls``.

    ``max_wall_seconds`` bounds how long one program waits on its host calls,
    or ``None`` to disable.  Past it a running call is cut off and every later
    one refused, as a ``TimeoutError`` the program catches to finish with what
    it has, so ``run_python`` bounds itself and ``llm.tool_timeout_seconds``
    leaves it out.

    The ``completion_*`` settings bound the ``complete`` function a program may
    call.  ``completion_max_calls`` is an exact budget per turn, shared by
    every program in it and raised inside the one that finds it spent, while
    each call also counts on the turn's usage and so against its request limit.
    ``completion_max_tokens`` caps one call's answer, with the effort's
    ``llm.thinking_budget_tokens`` on top where the runtime enforces that budget,
    ``completion_timeout_seconds`` bounds one call, and
    ``completion_concurrency`` is how many of a turn's calls are in flight at
    once, however wide its programs fan out with ``asyncio.gather``.
    """

    min_processes: int = 1
    max_processes: int | None = None
    checkout_timeout_seconds: float | None = 30.0
    request_timeout_seconds: float | None = 120.0
    execution_clock: Literal["cpu", "wall"] = "cpu"
    max_duration_seconds: float = 30.0
    max_memory_bytes: int = 1_000_000_000
    type_check: bool = True
    max_changeset_operations: int = 200
    max_changeset_deletes: int = 100
    max_changeset_chars: int = 100_000_000
    max_host_calls: int = Field(default=10_000, ge=1)
    max_suspensions: int = Field(default=100_000, ge=1)
    max_wall_seconds: float | None = Field(default=900.0, gt=0)
    completion_max_calls: int = Field(default=50, ge=0)
    completion_max_tokens: int = Field(default=8192, ge=1)
    completion_timeout_seconds: float = Field(default=300.0, gt=0)
    completion_concurrency: int = Field(default=4, ge=1)


class ConversationSettings(Section):
    """Lifetime of a conversation's folder on disk.

    The folder is ``<data_dir>/conversations/<user_id>/<conversation_id>/``, holding its
    ``/tmp`` and staged changesets alike.  Once untouched for ``ttl_hours``,
    its ``/tmp`` goes while the conversation exists, and the whole folder once
    it no longer does, in a sweep that runs in the background at startup and
    every ``sweep_interval_hours`` after.  A staged changeset of an existing
    conversation goes only when answered, and deleting the conversation
    deletes the folder at once.
    """

    ttl_hours: float = Field(default=72.0, gt=0)
    sweep_interval_hours: float = Field(default=1.0, gt=0)


class TmpSettings(Section):
    """Size of a conversation's ``/tmp`` folder.

    ``/tmp`` is ``<data_dir>/conversations/<user_id>/<conversation_id>/tmp/``, outside
    every workspace.  ``max_bytes`` caps what one ``/tmp`` may hold, checked on
    every write into it.
    """

    max_bytes: int = Field(default=100_000_000, ge=0)


class SkillSettings(Section):
    """Where the global skills come from, and what one skill may hold.

    ``dir`` is the operator's directory of global skills, one folder holding a
    ``SKILL.md`` each, read in place beside every enabled plugin's and never
    copied, which agents and users only read.  A user's and a group's skills
    live in ``<data_dir>/skills/<owner>/``, each capped at ``max_bytes`` in
    ``max_files`` files when it is installed, and every such library at
    ``library_max_bytes``, whether a user installs into it or the agent
    writes to it.
    """

    dir: Path | None = None
    max_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    max_files: int = Field(default=512, gt=0)
    library_max_bytes: int = Field(default=128 * 1024 * 1024, gt=0)


class AssistantSettings(Section):
    """Where the global assistants come from.

    ``dir`` is the operator's directory of global assistants, one
    ``<name>.json`` file each, read in place and never changed through
    Hivegent.  A user's and a group's assistants live in
    ``<data_dir>/assistants/<owner>/<name>.json``, written only through the
    REST API, so no agent tool reaches them.
    """

    dir: Path | None = None
