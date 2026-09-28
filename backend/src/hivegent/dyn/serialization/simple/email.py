from email import policy
from email.parser import Parser
from pathlib import Path

from html2text import html2text


def to_markdown(file_path: str | Path) -> tuple[str, str]:
    """
    Checks if a text file is in valid RFC (email) format.
    If valid RFC format, returns formatted string with subject and body.
    If custom format, extracts fields and returns formatted string.

    Args:
        file_path: Path to the text file

    Returns:
        Formatted string with subject and body content
    """
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Try to parse as RFC email format
    mail = Parser(policy=policy.default).parsestr(content)

    subject = mail.get("Subject", "(No Subject)")
    body = mail.get_body(preferencelist=("plain", "html"))
    body_text = html2text(body.get_content()) if body else "(No Body)"

    return subject, body_text
