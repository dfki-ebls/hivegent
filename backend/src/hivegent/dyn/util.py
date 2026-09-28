import asyncio
import hashlib
import shutil
import subprocess
import tempfile
from collections.abc import Awaitable, Callable, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import tiktoken


def hash_string(input: str) -> str:
    return hashlib.sha256(input.encode()).hexdigest()


def format_retrieved(retrieved_dict: dict[str, float]) -> str:
    ret = ""
    for tp, score in retrieved_dict.items():
        tp = tp.replace("\n", "")
        ret += f"- {tp} (relevance: {score})\n"
    return ret


@contextmanager
def convert_office_legacy(path: Path, to: Literal["xlsx", "docx"]) -> Generator[Path]:
    """LLM-GENERATED.
    Converts legacy "xls" and "doc" files to
    "xlsx"/"docx"

    Args:
        path (Path): The input document

    Returns:
        Iterator[Path]: A single .docx file path
    """
    if path.suffix.lower() in {".xlsx", ".docx"}:
        yield path
        return

    soffice = shutil.which("soffice")
    if soffice is None:
        raise RuntimeError(
            f"Converting {path} requires LibreOffice and soffice on PATH."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        _ = subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                to,
                "--outdir",
                tmpdir,
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        converted = Path(tmpdir) / f"{path.stem}.{to}"
        if not converted.is_file():
            raise RuntimeError(f"LibreOffice did not produce a .{to} for {path}")
        yield converted


enc = tiktoken.get_encoding("o200k_base")


def get_token_count(text: str) -> int:
    return len(enc.encode(text))


def truncate_to_tokens(text: str, limit: int) -> str:
    """LLM-GENERATED. Cut `text` down to at most `limit` tokens.

    Truncating on an estimated characters-per-token ratio silently overshoots on
    token-dense text (code, non-latin scripts), so cut on the token ids instead.
    """
    if limit <= 0:
        return ""
    tokens = enc.encode(text)
    if len(tokens) <= limit:
        return text
    # a cut inside a multi-byte character decodes to U+FFFD; drop it
    return enc.decode(tokens[:limit]).rstrip("\ufffd")


def upsert(value, key, d):
    if isinstance(value, list):
        if key in d:
            d[key] += value
        else:
            d[key] = value
    else:
        if key in d:
            d[key].append(value)
        else:
            d[key] = [value]


async def run_in_parallel[T, *Ts](
    async_func: Callable[[*Ts], Awaitable[T]],
    items: Sequence[tuple[*Ts]],
) -> list[T]:
    """
    Runs an async function against a list of items concurrently.

    Args:
        async_func: The coroutine function to call.
        items: A list of argument tuples, each unpacked into async_func.

    Returns:
        A list of results in the same order as the input items.
    """
    # Create a list of task objects
    tasks = [async_func(*params) for params in items]

    # Run them all concurrently and wait for the results
    results = await asyncio.gather(*tasks)

    return results
