"""The explore feature, the tools that read the workspaces."""

from collections.abc import Callable, Container, Mapping
from concurrent.futures import Executor
from functools import partial

from exar.ingest import MAX_FRAMES, vision_media_type
from exar.memory.mounts import Mounts
from exar.tools.files import Grep, Jq, Ls, Read, Ripgrep
from exar.tools.media import View
from exar.tools.table import Table
from pydantic_ai import RunContext

from ...cases import CASE_BASES, bindings
from ...config import settings
from ...media import ISOLATED
from ...retrieval import build_search_tool
from ...shared.formats import BINARY_SUFFIXES, BinaryContentMode
from ...shared.l10n import format_bytes
from ...tools.base import HIDDEN_ENTRIES, ToolFunc, query_hint, refusal_hint
from ...tools.cases import Limits, similar_cases
from ...tools.entries import Part, is_description_file, stem_path_from_reference
from ..common import WINDOW_MAX_CHARS, AgentTools, UserDeps
from ..guards import max_images

__all__ = [
    "GREP_GUIDANCE",
    "GREP_NOTES",
    "INJECTED",
    "LIST_NOTES",
    "VIEW",
    "explore_tools",
    "explore_toolset",
    "grep",
    "injected_tools",
    "jq",
    "list_documents",
    "query_table",
    "read_binary_document",
    "read_document",
    "shadowed",
]

_MAX_DOCUMENT_CHARS = settings.limits.max_file_size_bytes
"""The longest document a read decodes whole, every one the workspace accepts, as the sandbox reads."""

VIEW = "read_binary_document"
"""The name of the tool that shows images, PDFs, and videos, which `Read` points a model at."""

LIST_NOTES = (
    "The contents of ``.assets`` payload directories are hidden as well.  Any table, "
    "JSON, or BPMN file or folder of them is a case base that can be ranked by "
    "example with ``similar_cases``."
)

GREP_GUIDANCE = (
    "Suited to short exact terms, names, or codes and to structural matches\n"
    "through patterns, while search finds content by phrase across large\n"
    "collections and comes first."
)

GREP_NOTES = (
    "A document that matches both in its markdown description and in the\n"
    "original file it was projected from is reported once, under the\n"
    "description."
)


def shadowed(local: str, matched: Container[str]) -> bool:
    """Whether *local* is an original whose markdown description *matched* as well.

    Every entry keeps its markdown description beside the original it was
    projected from, so a text original (XML, CSV, source code) matches twice
    over with near-identical lines at shifted line numbers.  The description
    wins: it is the face of the entry the user edits and the chunker indexes,
    and it is regenerated whenever the original changes, so it never lags
    behind while the reverse does not hold.  An entry with no description hit
    is left alone, and a search globbed to the original's extension still
    reaches it, since then no description is in the result set to displace it.

    >>> shadowed("doc.xml", {"doc.xml", "doc.md"}), shadowed("doc.xml", {"doc.xml"})
    (True, False)
    """
    return (
        not is_description_file(local)
        and Part(stem_path_from_reference(local), "description").path in matched
    )


def list_documents(mounts: Mounts) -> ToolFunc:
    """The tool that lists the documents below *mounts*, which hides `.assets` payloads."""
    return Ls(
        mounts=mounts,
        name="list_documents",
        hidden=HIDDEN_ENTRIES,
        notes=LIST_NOTES,
        size=format_bytes,
    ).tool()


def read_document(
    mounts: Mounts,
    *,
    max_chars: int = 40_000,
    max_line_chars: int = 2000,
    max_document_chars: int = _MAX_DOCUMENT_CHARS,
) -> ToolFunc:
    """The tool that reads the documents below *mounts*, pointing a refused binary at `VIEW` and a table or JSON document at its query tool."""
    return Read(
        mounts=mounts,
        name="read_document",
        max_chars=max_chars,
        max_line_chars=max_line_chars,
        max_document_chars=max_document_chars,
        viewer=VIEW,
        hint=refusal_hint,
        media_type=vision_media_type,
        advice=query_hint,
    ).tool()


def read_binary_document(
    mounts: Mounts,
    *,
    binary_content_mode: BinaryContentMode = BinaryContentMode.IMAGES,
    max_frames: int = MAX_FRAMES,
    max_images: int | None = None,
    executor: Executor | None = ISOLATED,
) -> ToolFunc:
    """The tool that shows the images, PDFs, and videos below *mounts*, decoded in the isolating *executor*."""
    return View(
        mounts=mounts,
        name=VIEW,
        pdf="native" if binary_content_mode is BinaryContentMode.NATIVE else "images",
        max_frames=max_frames,
        max_images=max_images,
        hint=refusal_hint,
        executor=executor,
    ).tool()


def grep(mounts: Mounts) -> ToolFunc:
    """The tool that searches the documents below *mounts* with ripgrep, dropping an original whose description matches too."""
    return Grep(
        mounts=mounts,
        engine=Ripgrep(),
        binary_suffixes=BINARY_SUFFIXES,
        hidden=HIDDEN_ENTRIES,
        guidance=GREP_GUIDANCE,
        notes=GREP_NOTES,
        hint=refusal_hint,
        shadowed=shadowed,
    ).tool()


def jq(mounts: Mounts) -> ToolFunc:
    """The tool that filters the JSON documents below *mounts* with jq."""
    return Jq(
        mounts=mounts, max_document_chars=_MAX_DOCUMENT_CHARS, hint=refusal_hint
    ).tool()


def query_table(
    mounts: Mounts, *, max_cell_chars: int = 200, max_named_columns: int = 10
) -> ToolFunc:
    """The tool that queries the tabular documents below *mounts* with SQL."""
    return Table(
        mounts=mounts,
        max_cell_chars=max_cell_chars,
        max_named_columns=max_named_columns,
        max_document_chars=_MAX_DOCUMENT_CHARS,
        hint=refusal_hint,
    ).tool()


# None of them is deferred.  `query_table`, `jq`, and `read_binary_document`
# were, on the reasoning that a document of that shape is rare enough that most
# turns should not pay for the schema, and that `read_document` naming the tool
# on its refusal writes the discovery query for the model.  Naming it is not
# enough: a model that cannot see the name in its tool list reads the pointer as
# describing a tool it was not given and works around it instead of searching,
# which is how a spreadsheet run ends up parsing the markdown projection by
# hand.  A tool is registered eagerly or not at all, and a deployment that
# would rather not pay for one names it in `settings.tools.disabled`.
# The read tools take the mounts of the run as a function, read when a tool is called.
type _Builder = Callable[[RunContext[UserDeps]], ToolFunc]

_BUILDERS: Mapping[str, _Builder] = {
    "list_documents": lambda ctx: list_documents(ctx.deps.mounts),
    "read_document": lambda ctx: read_document(
        ctx.deps.mounts, max_chars=WINDOW_MAX_CHARS
    ),
    VIEW: lambda ctx: read_binary_document(
        ctx.deps.mounts,
        binary_content_mode=settings.multimodal.binary_content,
        max_images=max_images(ctx),
    ),
    "query_table": lambda ctx: query_table(ctx.deps.mounts),
    "jq": lambda ctx: jq(ctx.deps.mounts),
    "similar_cases": lambda ctx: similar_cases(
        ctx.deps.search_paths(),
        limits=Limits(max_bytes=settings.limits.max_file_size_bytes),
        bind=partial(bindings, ctx.deps.llm),
        cache=CASE_BASES,
    ),
    "grep": lambda ctx: grep(ctx.deps.mounts),
    "search": lambda ctx: build_search_tool(ctx.deps.reach),
}
"""Every explore tool by name, built for one run, which the sandbox picks from too."""

INJECTED = ("query_table", "search")
"""The explore tools a program is handed (``agents/tools/python.py``).

Retrieval reaches the database and a spreadsheet needs a decoder the
interpreter lacks, while everything else the mount already provides: ``open``
is the read tools, ``re`` is grep, and ``json`` is jq.  The web tools stay out,
since a program could loop over the links of a planted page.
"""


def explore_tools(ctx: RunContext[UserDeps]) -> list[ToolFunc]:
    """The tools that read what the run's user sees, for one run."""
    return [build(ctx) for build in _BUILDERS.values()]


def injected_tools(ctx: RunContext[UserDeps]) -> list[ToolFunc]:
    """The :data:`INJECTED` explore tools, built alone for one run."""
    return [_BUILDERS[name](ctx) for name in INJECTED]


explore_toolset = AgentTools(factory=explore_tools, seeing=frozenset({VIEW}))
"""The explore tools, held to the host policy, `VIEW` shown only to a model that sees images."""
