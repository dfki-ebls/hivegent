"""Parity: every instruction block the main agent reads exists in German."""

from datetime import date

import pytest

from hivegent import prompts
from hivegent.prompts import compose_instructions
from hivegent.shared.l10n import Localized

_TODAY = date(2026, 1, 1)

_BLOCKS = {
    **{
        name: value
        for name, value in vars(prompts).items()
        if isinstance(value, Localized)
    },
    "sandbox_api_instructions": prompts.sandbox_api_instructions("x"),
    "date_note": prompts.date_note(_TODAY),
}


@pytest.mark.parametrize("name", sorted(_BLOCKS))
def test_every_block_is_translated(name: str) -> None:
    german = str(_BLOCKS[name].de)

    assert german.strip()
    assert german != str(_BLOCKS[name].en)


def test_the_composed_instructions_end_on_the_german_pin() -> None:
    german = compose_instructions("de")

    assert german != compose_instructions("en")
    assert german.endswith(
        "Antworte immer auf Deutsch, außer die Benutzer:in schreibt in einer "
        "anderen Sprache."
    )
