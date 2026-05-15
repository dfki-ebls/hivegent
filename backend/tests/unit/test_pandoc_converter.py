"""Tests for the Pandoc converter config boundary."""

import pytest
from pydantic import ValidationError

from hivegent.converters import ConversionPipeline, get_converter


def test_pandoc_rejects_extra_cli_args() -> None:
    """Pandoc config does not accept arbitrary subprocess arguments."""
    with pytest.raises(ValidationError):
        get_converter(
            ConversionPipeline.PANDOC,
            filename="note.txt",
            config={"extra_args": ["--output=/tmp/owned"]},
        )
