"""Reasoning-effort to request-settings mapping for self-hosted endpoints."""

from dataclasses import replace

import pytest

from hivegent.config import settings
from hivegent.config.llm import InferenceProvider, LlmProviderSettings
from hivegent.llm import (
    SUMMARY_MAX_TOKENS,
    summary_model_settings,
    thinking_model_settings,
)
from hivegent.llm_config import LlmConfig
from hivegent.security import OperatorUrl

SELF_HOSTED = LlmConfig(
    model="qwen",
    provider=LlmProviderSettings(
        base_url=OperatorUrl("http://127.0.0.1:18101"),
        inference_provider=InferenceProvider.LLAMA_CPP,
    ),
    reasoning=("low", "medium", "high"),
)
QWEN38 = LlmConfig(
    model="Qwen/Qwen3.8-27B",
    provider=LlmProviderSettings(
        base_url=OperatorUrl("http://127.0.0.1:18101"),
        inference_provider=InferenceProvider.VLLM,
    ),
    reasoning=("low", "medium", "high"),
    reasoning_overrides={"high": "xhigh"},
)
OPENAI = LlmConfig(
    model="gpt",
    provider=LlmProviderSettings(
        base_url=OperatorUrl("https://api.openai.com/v1"),
        inference_provider=InferenceProvider.OPENAI,
    ),
    reasoning=("low", "medium", "high"),
)


BUDGETS = settings.llm.thinking_budget_tokens
SGLANG = replace(
    SELF_HOSTED,
    provider=SELF_HOSTED.provider.model_copy(
        update={"inference_provider": InferenceProvider.SGLANG}
    ),
)
THINKING_ON = {"chat_template_kwargs": {"enable_thinking": True}}
THINKING_OFF = {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.mark.parametrize(
    ("config", "thinking", "extra_body"),
    [
        (
            replace(SELF_HOSTED, thinking="medium"),
            "medium",
            {**THINKING_ON, "thinking_budget_tokens": BUDGETS["medium"]},
        ),
        (
            replace(QWEN38, thinking="high"),
            "xhigh",
            {**THINKING_ON, "thinking_token_budget": BUDGETS["high"]},
        ),
        (replace(SGLANG, thinking="high"), "high", THINKING_ON),
        (SELF_HOSTED, False, THINKING_OFF),
        (
            replace(SELF_HOSTED, reasoning=("medium",), thinking="high"),
            "medium",
            {**THINKING_ON, "thinking_budget_tokens": BUDGETS["medium"]},
        ),
        (replace(SELF_HOSTED, reasoning=(), thinking="high"), False, THINKING_OFF),
        (
            replace(SELF_HOSTED, reasoning=None, thinking="high"),
            "high",
            {**THINKING_ON, "thinking_budget_tokens": BUDGETS["high"]},
        ),
        (replace(OPENAI, thinking="high"), "high", None),
    ],
    ids=[
        "level-selects-budget",
        "qwen38-native-xhigh",
        "sglang-without-budget",
        "false-disables",
        "clamped-to-model",
        "no-levels-disables",
        "unset-passes-through",
        "openai-no-self-hosted-fields",
    ],
)
def test_the_effort_maps_to_the_request_settings(
    config: LlmConfig, thinking: str | bool, extra_body: dict[str, object] | None
) -> None:
    """A level beyond what the model offers is clamped, and an OpenAI endpoint gets no self-hosted fields."""
    model_settings = thinking_model_settings(config)

    assert model_settings.get("thinking") == thinking
    assert model_settings.get("extra_body") == extra_body


def test_summary_settings_turn_reasoning_off_and_cap_the_completion() -> None:
    model_settings = summary_model_settings(replace(SELF_HOSTED, thinking="high"))

    assert model_settings.get("thinking") is False
    assert model_settings.get("extra_body") == THINKING_OFF
    assert model_settings.get("max_tokens") == SUMMARY_MAX_TOKENS


def test_answer_cap_carries_an_enforced_thinking_budget_on_top() -> None:
    def cap(config: LlmConfig) -> int | None:
        return thinking_model_settings(config, 100).get("max_tokens")

    assert cap(SELF_HOSTED) == 100
    assert cap(replace(SELF_HOSTED, thinking="medium")) == 100 + BUDGETS["medium"]
    assert cap(replace(SELF_HOSTED, thinking="medium", max_tokens=50)) == 50
    assert cap(replace(OPENAI, thinking="medium")) == 100
