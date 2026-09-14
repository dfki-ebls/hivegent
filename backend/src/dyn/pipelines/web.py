import asyncio

from dyn.serialization.web import crawl_and_merge, get_single_webpage_md

from .markdown import to_chunks as to_chunks_md


def to_chunks(url: str, is_single: bool = True) -> list[str]:
    if is_single:
        md = get_single_webpage_md(url)
    else:
        md = asyncio.run(crawl_and_merge(url))
    # return to_chunks_md(md)
    return [md]
