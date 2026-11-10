"""Gold benchmark entries for RAGold, built from a conversation turn.

RAGold imports a zip holding ``annotations.json`` (schema version 3) and the
original documents under ``files/<documentId>/<name>``.  The archive carries
one finished entry, with its key facts.
"""

import hashlib
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import IO, Literal, Self
from uuid import uuid4
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from markdown_it import MarkdownIt
from markdown_it.token import Token
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from .l10n import Language

__all__ = [
    "Answerability",
    "KeyFactImportance",
    "PassageGrade",
    "QuestionType",
    "RagoldKeyFact",
    "RagoldPassage",
    "RagoldRequest",
    "build_archive",
    "iter_archive",
    "plain_text",
]

type QuestionType = Literal[
    "simple",
    "simple_w_condition",
    "comparison",
    "aggregation",
    "set",
    "multi-hop",
    "post-processing",
    "false_premise",
]
"""CRAG question types (Yang et al. 2024, https://doi.org/10.52202/079017-0335)."""

type Answerability = Literal["answerable", "partial", "unanswerable"]
"""MTRAG answerability (Katsis et al. 2025, arXiv 2501.03468)."""

type PassageGrade = Literal["needed", "helpful", "misleading"]

type KeyFactImportance = Literal["must", "nice"]

SCHEMA_VERSION = 3

# Archives up to this size stay in memory, larger ones roll over to disk.
_SPOOL_MEMORY = 32 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024
_MARKDOWN = MarkdownIt("commonmark").enable("table")
_HTML_TAG = re.compile(r"<[^>]*>")
_SEPARATOR_ROW = re.compile(r"(?=.*\|)(?=.*-)[\s|:-]+")


def _inline_text(children: Sequence[Token]) -> str:
    """Join the text of inline tokens, dropping their markup and HTML."""
    parts: list[str] = []

    for child in children:
        match child.type:
            case "text" | "code_inline":
                parts.append(child.content)
            case "image":
                parts.append(_inline_text(child.children or ()))
            case "softbreak" | "hardbreak":
                parts.append("\n")
            case "html_inline" if child.content.lower().startswith("<br"):
                parts.append("\n")

    return "".join(parts)


def _text_lines(text: str) -> Iterator[str]:
    """Yield the lines of a block, recovering table rows cut off from their header."""
    for line in text.splitlines():
        stripped = line.strip()

        if _SEPARATOR_ROW.fullmatch(stripped):
            continue

        if stripped.startswith("|") or stripped.endswith("|"):
            cells = stripped.removeprefix("|").removesuffix("|").split("|")
            yield "\t".join(cell.strip() for cell in cells)
        else:
            yield line


def plain_text(markdown: str) -> str:
    r"""Render a markdown snippet as plain text, one block per line.

    Table cells are joined by tabs, code blocks are kept verbatim,
    and HTML tags are dropped.
    Rows cut off from their table header still split into cells,
    while other constructs cut off mid-way keep their literal text.

    >>> plain_text("# Bees\n\nThey **pollinate** [flowers](https://bees.test).")
    'Bees\nThey pollinate flowers.'
    >>> plain_text("| Kind | Count |\n|---|--:|\n| `worker` | 3 |")
    'Kind\tCount\nworker\t3'
    """
    lines: list[str] = []
    row: list[str] | None = None

    for token in _MARKDOWN.parse(markdown):
        match token.type:
            case "tr_open":
                row = []
            case "tr_close":
                lines.append("\t".join(row or ()))
                row = None
            case "inline" if row is not None:
                row.append(_inline_text(token.children or ()).replace("\n", " "))
            case "inline":
                lines.extend(_text_lines(_inline_text(token.children or ())))
            case "fence" | "code_block":
                lines.append(token.content.removesuffix("\n"))
            case "html_block":
                lines.extend(_text_lines(_HTML_TAG.sub("", token.content)))

    return "\n".join(line for line in lines if line.strip())


class _Camel(BaseModel):
    """RAGold's camelCase wire format."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


class RagoldPassage(_Camel):
    """A passage of a document that bears on the answer."""

    path: str = Field(description="Canonical workspace path of the document")
    text: str = Field(description="Markdown, shipped as plain text")
    grade: PassageGrade

    @field_validator("text")
    @classmethod
    def _plain(cls, text: str) -> str:
        plain = plain_text(text)

        if not plain:
            raise ValueError("Passage has no text besides markdown syntax")

        return plain


class RagoldKeyFact(_Camel):
    """A verbatim excerpt of the response that a good answer has to contain."""

    text: str = Field(min_length=1)
    importance: KeyFactImportance


class _Entry(_Camel):
    """The fields a request shares with the annotation it becomes."""

    query: str = Field(min_length=1)
    response: str = Field(min_length=1)
    question_type: QuestionType
    answerability: Answerability
    origin: str = Field(
        min_length=1, description="URL of the conversation the entry comes from"
    )
    notes: str = ""
    key_facts: list[RagoldKeyFact] = []

    @model_validator(mode="after")
    def _key_facts_are_excerpts(self) -> Self:
        if missing := [
            fact.text for fact in self.key_facts if fact.text not in self.response
        ]:
            raise ValueError(f"Key facts missing from the response: {missing}")

        return self


class RagoldRequest(_Entry):
    """One conversation turn as a RAGold entry, with the documents it ships."""

    passages: list[RagoldPassage] = []
    documents: list[str] = Field(
        default=[], description="Canonical workspace paths of the shipped documents"
    )

    @model_validator(mode="after")
    def _passages_are_shipped(self) -> Self:
        missing = {item.path for item in self.passages}.difference(self.documents)

        if missing:
            raise ValueError(f"Passages name unlisted documents: {sorted(missing)}")

        return self


class _Stamped(_Camel):
    """A record created now and unchanged since."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda data: data["created_at"])


class _Document(_Stamped):
    name: str
    size: int
    sha256: str


class _Passage(_Camel):
    document_id: str
    text: str
    grade: PassageGrade


class _Annotation(_Entry, _Stamped):
    passages: list[_Passage]


class _Envelope(_Stamped):
    version: Literal[3] = SCHEMA_VERSION
    author: str = Field(min_length=1)
    project: str = Field(min_length=1)
    language: Language
    annotations: dict[str, _Annotation]
    documents: dict[str, _Document]


def _write_document(archive: ZipFile, file: Path, arcname: str) -> tuple[int, str]:
    """Copy *file* into *archive*, returning its size and SHA-256 digest."""
    info = ZipInfo.from_file(file, arcname, strict_timestamps=False)
    digest = hashlib.sha256()

    with file.open("rb") as source, archive.open(info, "w") as target:
        while chunk := source.read(_CHUNK_SIZE):
            digest.update(chunk)
            target.write(chunk)

    # Writing sets the size to the bytes actually copied.
    return info.file_size, digest.hexdigest()


def _write_archive(
    out: IO[bytes],
    request: RagoldRequest,
    files: Mapping[str, Path],
    *,
    author: str,
    project: str,
    language: Language,
) -> None:
    # Paths naming the same file, such as an entry's description and its original, share a document.
    ids = {file: str(uuid4()) for file in files.values()}
    documents: dict[str, _Document] = {}

    with ZipFile(out, "w", ZIP_STORED) as archive:
        for file, document_id in ids.items():
            size, sha256 = _write_document(
                archive, file, f"files/{document_id}/{file.name}"
            )
            documents[document_id] = _Document(name=file.name, size=size, sha256=sha256)

        annotation = _Annotation(
            **request.model_dump(by_alias=False, exclude={"passages", "documents"}),
            passages=[
                _Passage(
                    document_id=ids[files[item.path]], text=item.text, grade=item.grade
                )
                for item in request.passages
            ],
        )
        envelope = _Envelope(
            author=author,
            project=project,
            language=language,
            annotations={str(uuid4()): annotation},
            documents=documents,
        )
        archive.writestr("annotations.json", envelope.model_dump_json(indent=2))


def build_archive(
    request: RagoldRequest,
    files: Mapping[str, Path],
    *,
    author: str,
    project: str,
    language: Language,
) -> SpooledTemporaryFile[bytes]:
    """Write the RAGold zip of *request*, rewound for reading.

    *files* maps every path of ``request.documents`` to the file it ships.
    Blocking: it reads every document.
    """
    with ExitStack() as cleanup:
        spool = cleanup.enter_context(SpooledTemporaryFile(max_size=_SPOOL_MEMORY))
        _write_archive(
            spool, request, files, author=author, project=project, language=language
        )
        spool.seek(0)
        # Built in full, so the caller owns the open archive from here.
        cleanup.pop_all()

    return spool


def iter_archive(spool: SpooledTemporaryFile[bytes]) -> Iterator[bytes]:
    """Stream a built archive, closing it once read or abandoned."""
    with spool:
        while chunk := spool.read(_CHUNK_SIZE):
            yield chunk
