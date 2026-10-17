"""Reasoning-effort to request-settings mapping for self-hosted endpoints."""

from hivegent.config import InferenceProvider
from hivegent.llm import (
    _THINKING_BUDGET_TOKENS,
    SUMMARY_MAX_TOKENS,
    summary_model_settings,
    thinking_model_settings,
)
from hivegent.llm_config import LlmConfig

SELF_HOSTED = LlmConfig(
    model="qwen",
    base_url="http://127.0.0.1:18101",
    inference_provider=InferenceProvider.LLAMA_CPP,
)
QWEN38 = LlmConfig(
    model="Qwen/Qwen3.8-27B",
    base_url="http://127.0.0.1:18101",
    inference_provider=InferenceProvider.VLLM,
)
OPENAI = LlmConfig(
    model="gpt",
    base_url="https://api.openai.com/v1",
    inference_provider=InferenceProvider.OPENAI,
)


def test_self_hosted_level_selects_its_budget() -> None:
    settings = thinking_model_settings("medium", SELF_HOSTED)

    assert settings.get("thinking") == "medium"
    assert settings.get("extra_body") == {
        "chat_template_kwargs": {"enable_thinking": True},
        "thinking_budget_tokens": _THINKING_BUDGET_TOKENS["medium"],
    }


def test_qwen38_maps_high_to_native_xhigh_with_high_budget() -> None:
    settings = thinking_model_settings("high", QWEN38)

    assert settings.get("thinking") == "xhigh"
    assert settings.get("extra_body") == {
        "chat_template_kwargs": {"enable_thinking": True},
        "thinking_token_budget": _THINKING_BUDGET_TOKENS["high"],
    }


def test_false_disables_thinking_without_a_budget() -> None:
    extra_body = thinking_model_settings(False, SELF_HOSTED).get("extra_body")

    assert extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_openai_endpoint_never_receives_self_hosted_fields() -> None:
    settings = thinking_model_settings("high", OPENAI)

    assert settings.get("thinking") == "high"
    assert "extra_body" not in settings


def test_summary_settings_disable_reasoning_and_cap_the_completion() -> None:
    settings = summary_model_settings(SELF_HOSTED)

    assert settings.get("thinking") is False
    assert settings.get("max_tokens") == SUMMARY_MAX_TOKENS
    assert settings.get("extra_body") == {
        "chat_template_kwargs": {"enable_thinking": False}
    }
