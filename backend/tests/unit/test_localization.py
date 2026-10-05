"""Parity: every instruction block the main agent reads exists in German."""

import pytest

from hivegent import prompts
from hivegent.l10n import Localized
from hivegent.prompts import Personality, compose_instructions

_BLOCKS = {
    **{
        name: value
        for name, value in vars(prompts).items()
        if isinstance(value, Localized)
    },
    "memory_instructions": prompts.memory_instructions("x"),
    "sandbox_api_instructions": prompts.sandbox_api_instructions("x"),
}


@pytest.mark.parametrize("name", sorted(_BLOCKS))
def test_every_block_is_translated(name: str) -> None:
    german = str(_BLOCKS[name].de)

    assert german.strip()
    assert german != str(_BLOCKS[name].en)


@pytest.mark.parametrize("personality", list(Personality))
def test_the_composed_instructions_end_on_the_german_pin(
    personality: Personality,
) -> None:
    german = compose_instructions(personality, "", "de")

    assert german != compose_instructions(personality, "", "en")
    assert german.endswith(
        "Antworte immer auf Deutsch, außer die Benutzer:in schreibt in einer "
        "anderen Sprache."
    )


def test_every_personality_is_translated() -> None:
    for template in prompts.PERSONALITY_TEMPLATES.values():
        assert template.de.strip() and template.de != template.en
