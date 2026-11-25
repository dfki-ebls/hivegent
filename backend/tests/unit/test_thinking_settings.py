"""Reasoning-effort to request-settings mapping for self-hosted endpoints."""

from dataclasses import replace

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


def test_self_hosted_level_selects_its_budget() -> None:
    model_settings = thinking_model_settings(replace(SELF_HOSTED, thinking="medium"))

    assert model_settings.get("thinking") == "medium"
    assert model_settings.get("extra_body") == {
        "chat_template_kwargs": {"enable_thinking": True},
        "thinking_budget_tokens": settings.llm.thinking_budget_tokens["medium"],
    }


def test_qwen38_maps_high_to_native_xhigh_with_high_budget() -> None:
    model_settings = thinking_model_settings(replace(QWEN38, thinking="high"))

    assert model_settings.get("thinking") == "xhigh"
    assert model_settings.get("extra_body") == {
        "chat_template_kwargs": {"enable_thinking": True},
        "thinking_token_budget": settings.llm.thinking_budget_tokens["high"],
    }


def test_sglang_switches_thinking_without_a_budget() -> None:
    sglang = replace(
        SELF_HOSTED,
        provider=SELF_HOSTED.provider.model_copy(
            update={"inference_provider": InferenceProvider.SGLANG}
        ),
    )

    assert thinking_model_settings(replace(sglang, thinking="high")).get(
        "extra_body"
    ) == {"chat_template_kwargs": {"enable_thinking": True}}


def test_false_disables_thinking_without_a_budget() -> None:
    extra_body = thinking_model_settings(SELF_HOSTED).get("extra_body")

    assert extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_unsupported_levels_are_clamped_to_the_model() -> None:
    medium_only = replace(SELF_HOSTED, reasoning=("medium",))

    assert (
        thinking_model_settings(replace(medium_only, thinking="high")).get("thinking")
        == "medium"
    )
    assert (
        thinking_model_settings(
            replace(SELF_HOSTED, reasoning=(), thinking="high")
        ).get("thinking")
        is False
    )


def test_unset_levels_pass_the_effort_through() -> None:
    unset = replace(SELF_HOSTED, reasoning=None, thinking="high")

    assert thinking_model_settings(unset).get("thinking") == "high"


def test_openai_endpoint_never_receives_self_hosted_fields() -> None:
    model_settings = thinking_model_settings(replace(OPENAI, thinking="high"))

    assert model_settings.get("thinking") == "high"
    assert "extra_body" not in model_settings


def test_summary_settings_turn_reasoning_off_and_cap_the_completion() -> None:
    model_settings = summary_model_settings(replace(SELF_HOSTED, thinking="high"))

    assert model_settings.get("thinking") is False
    assert model_settings.get("extra_body") == {
        "chat_template_kwargs": {"enable_thinking": False}
    }
    assert model_settings.get("max_tokens") == SUMMARY_MAX_TOKENS


def test_answer_cap_carries_an_enforced_thinking_budget_on_top() -> None:
    budget = settings.llm.thinking_budget_tokens["medium"]

    def cap(config: LlmConfig) -> int | None:
        return thinking_model_settings(config, 100).get("max_tokens")

    assert cap(SELF_HOSTED) == 100
    assert cap(replace(SELF_HOSTED, thinking="medium")) == 100 + budget
    assert cap(replace(SELF_HOSTED, thinking="medium", max_tokens=50)) == 50
    assert cap(replace(OPENAI, thinking="medium")) == 100
