"""Single-chunk pipeline for generated asset descriptions."""

from dataclasses import dataclass

from .base import ChunkData, DocumentChunker

__all__ = ["NoneDocumentChunker"]


@dataclass(slots=True, frozen=True)
class NoneDocumentChunker(DocumentChunker):
    """Chunker that returns the full text as one chunk."""

    name = "none"
    label = "None"
    description = "Keep the full document as a single chunk"

    async def _split(
        self,
        text: str,
        /,
        *,
        mime: str | None = None,
    ) -> list[ChunkData]:
        """Return a single chunk spanning the full text."""
        return [
            ChunkData(
                text=text,
                # Word count approximation; sufficient for generated stubs.
                token_count=max(len(text.split()), 1) if text else 0,
                start_index=0,
                end_index=len(text),
                index=0,
            )
        ]
