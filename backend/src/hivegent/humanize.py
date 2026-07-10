"""Human-readable formatting helpers shared across the CLI and API messages."""

__all__ = ["format_bytes"]


def format_bytes(size: int) -> str:
    """Render a byte count using the largest unit that keeps it at or above one.

    Mirrors the frontend's ``formatFileSize`` so a size shown in an API error and
    the same size shown in the UI read identically.

    Args:
        size: The number of bytes.

    Returns:
        The size as ``B``, ``KB``, or ``MB``, with one decimal place from KB up.

    Examples:
        >>> format_bytes(512)
        '512 B'
        >>> format_bytes(52428800)
        '50.0 MB'
    """
    if size < 1024:
        return f"{size} B"

    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"

    return f"{size / (1024 * 1024):.1f} MB"
