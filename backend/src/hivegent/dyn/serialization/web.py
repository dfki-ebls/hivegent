import asyncio
import os

import requests
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
from dotenv import load_dotenv

_ = load_dotenv()


def get_single_webpage_md(url: str) -> str:
    """Extracts the content of a single webpage using Jina Reader.

    Args:
        url (str): The input URL

    Returns:
        str: The extracted Markdown content
    """
    url = f"https://r.jina.ai/{url}"
    print("JINA_API_KEY", os.getenv("JINA_API_KEY"))
    headers = {"Authorization": f"Bearer {os.getenv('JINA_API_KEY')}"}

    response = requests.get(url, headers=headers)
    text = response.text
    marker = "Markdown Content:\n"
    return text[text.index(marker) + len(marker) :]


async def crawl_and_merge(url: str) -> str:
    """Extracts the content of an entire website using Crawl4AI. Results in a single large Markdown string.

    Args:
        url (str): The URL to crawl

    Returns:
        str: The merged markdown content of successful pages
    """
    config = CrawlerRunConfig(
        deep_crawl_strategy=BFSDeepCrawlStrategy(max_depth=2, include_external=False),
        scraping_strategy=LXMLWebScrapingStrategy(),
        verbose=True,
    )
    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun(url, config=config)
        print(f"Crawled {len(results)} pages in total")

        # Access individual results
        ret = ""
        for result in results:
            if not result.success or result.markdown is None:
                print(f"Skipping {result.url}: {result.error_message}")
                continue
            ret += result.markdown.fit_markdown
        return ret


def to_md(url: str, is_single: bool = True) -> str:
    if is_single:
        md = get_single_webpage_md(url)
    else:
        md = asyncio.run(crawl_and_merge(url))
    return md
