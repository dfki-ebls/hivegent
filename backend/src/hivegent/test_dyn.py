import asyncio
from pathlib import Path

from hivegent.chunkers import ChunkingPipeline, get_chunker
from hivegent.converters import ConversionPipeline, get_converter

text = """# Test Markdown Document

This is a sample Markdown file created to test rendering of common elements: headings, paragraphs, inline formatting, lists, code, and more.

## Introduction

Markdown is a lightweight markup language that lets you write using an easy-to-read, easy-to-write plain text format. It's widely used for **README files**, *documentation*, and ***formatted*** chat messages.

You can also add `inline code` snippets, ~~strikethrough text~~, and [links to other sites](https://www.markdownguide.org) with ease.

## Lists

### Unordered List

- First item
- Second item
  - Nested item A
  - Nested item B
- Third item

### Ordered List

1. Preheat the oven
2. Mix the ingredients
3. Bake for 25 minutes

## Code Block

Here's a small snippet of Python code:

```python
def greet(name):
    return f"Hello, {name}!"

print(greet("World"))
```

## Blockquote

> The best way to predict the future is to invent it.
> — Alan Kay

## Table

| Feature      | Supported | Notes                  |
|--------------|-----------|-------------------------|
| Headings     | ✅        | H1 through H6           |
| Lists        | ✅        | Ordered & unordered     |
| Tables       | ✅        | Pipe syntax              |
| Footnotes    | ⚠️        | Depends on renderer      |

## Horizontal Rule

Below is a horizontal rule:

---

## Conclusion

This document covers most of the common Markdown syntax elements in a single file, making it useful for quickly testing how a renderer handles headings, emphasis, lists, code, quotes, tables, and rules.
"""


def test_dyn_converter() -> str:
    converter = get_converter(ConversionPipeline.DYN_MARKDOWN, filename="test.md")
    result = asyncio.run(converter(Path("/Users/kilian/Downloads/test.md")))
    return result.markdown


def test_dyn_chunker() -> None:
    converted = test_dyn_converter()
    chunker = get_chunker(ChunkingPipeline.DYN)
    chunks = asyncio.run(chunker(converted))

    assert chunks
    for chunk in chunks:
        print(f"--- chunk ({chunk.token_count} tokens) ---")
        print(chunk.text)
        print((chunk.start_line, chunk.end_line))
        print((chunk.start_index, chunk.end_index))


if __name__ == "__main__":
    # test_dyn_converter()
    test_dyn_chunker()
