from pathlib import Path

from .email import to_chunks as email_chunks
from .excel import to_chunks as excel_chunks
from .json import to_chunks as json_chunks
from .markdown import to_chunks as markdown_chunks
from .pdf import to_chunks as pdf_chunks
from .word import to_chunks as word_chunks


def to_chunks(inpath: Path, limit: int = 2048) -> list[str]:
    """Return the appropriate chunked content based on the file extension.

    Args:
        inpath (Path): The path to the document
        limit (int): The maximum chunk size

    Returns:
        list[str]: A list of document chunks
    """
    ext = inpath.suffix

    if ext in {".txt", ".eml", ".msg"}:
        return email_chunks(inpath, limit)
    if ext in {".xls", ".xlsx"}:
        return excel_chunks(inpath, limit)
    if ext in {".json", ".jsonl"}:
        return json_chunks(inpath, limit)
    if ext in {".doc", ".docx"}:
        return word_chunks(inpath, limit)
    if ext == ".md":
        return markdown_chunks(inpath, limit)
    if ext == ".pdf":
        return pdf_chunks(inpath, limit)
    raise ValueError("Unknown document format!")
