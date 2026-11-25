"""Settings of the size limits and the rate limits."""

from pydantic import PositiveFloat, PositiveInt

from .common import Section

__all__ = [
    "LimitsSettings",
    "RateLimit",
    "RateLimitSettings",
    "TeamsRateLimitSettings",
    "UserRateLimits",
]


class LimitsSettings(Section):
    """Upload, collection, and decoder size/count limits.

    ``max_image_pixels`` raises Pillow's decompression-bomb threshold in every
    pool worker (`exar.ingest.limit_worker`) and docling's own image
    bound, so large scans and images embedded in PDFs, Office documents, or
    web pages decode successfully; the value still guards against truly
    degenerate inputs.

    ``max_attachment_bytes`` bounds one image attached to a chat turn.  It
    sits well below ``max_file_size_bytes`` because an attachment rides in
    the conversation's message history and is re-sent to the model on every
    later turn, so its cost is paid per request rather than once.

    ``max_memory_chars`` bounds a user's memory, which is reported to the
    model at the start of every conversation.
    """

    max_file_size_bytes: int = 50 * 1024 * 1024  # 50 MB
    max_collection_size_bytes: int = 512 * 1024 * 1024  # 512 MB
    max_collection_files: int = 10_000
    max_image_pixels: int = 1_000_000_000  # ~3 GB uncompressed
    max_attachment_bytes: int = 10 * 1024 * 1024  # 10 MB
    max_memory_chars: int = 20_000


class RateLimit(Section):
    """A token bucket: ``burst`` requests at once, refilled at ``per_minute``."""

    burst: PositiveInt
    per_minute: PositiveFloat


class UserRateLimits(Section):
    """Per-user rate limits on the routes that spend a model or the pipeline.

    ``request`` bounds every authenticated request, to the REST API and to
    ``/mcp`` alike, and the others bound the costly routes on top of it.
    ``chat`` bounds chat turns, ``resume`` the requests answering the
    approvals and questions a turn awaits, which continue it rather than start
    one and only follow a turn that asked, ``upload`` what runs the pipeline,
    uploads, reconversions, rechunks, and workspace changes,
    ``transcription`` speech input, ``generation`` what computes an answer
    on demand, the titles and asset descriptions the aux model writes, case
    base queries, and conversation archives, and ``detection`` watermark
    checks.
    """

    request: RateLimit = RateLimit(burst=300, per_minute=600)
    chat: RateLimit = RateLimit(burst=20, per_minute=10)
    resume: RateLimit = RateLimit(burst=100, per_minute=60)
    upload: RateLimit = RateLimit(burst=200, per_minute=60)
    transcription: RateLimit = RateLimit(burst=10, per_minute=10)
    generation: RateLimit = RateLimit(burst=20, per_minute=20)
    detection: RateLimit = RateLimit(burst=10, per_minute=10)


class TeamsRateLimitSettings(UserRateLimits):
    """The limits of the Teams bridge, the one service principal, in place of a person's.

    All Teams users share its one identity and so each of its buckets, so
    ``request`` and ``chat`` default far above a person's.  It never names
    who asks, since the backend could not verify it.
    """

    request: RateLimit = RateLimit(burst=3000, per_minute=6000)
    chat: RateLimit = RateLimit(burst=200, per_minute=120)


class RateLimitSettings(UserRateLimits):
    """Per-user rate limits, with ``teams`` replacing all of them for the Teams bridge.

    Each server process enforces them on its own, so several servers behind
    one address each grant the full limit.

    Configurable via ``HIVEGENT_RATE_LIMITS__*`` environment variables.
    """

    teams: TeamsRateLimitSettings = TeamsRateLimitSettings()
