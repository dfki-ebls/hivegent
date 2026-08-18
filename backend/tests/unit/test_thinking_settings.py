"""Reasoning-effort to request-settings mapping for self-hosted endpoints."""

from hivegent.config import InferenceProvider
from hivegent.llm import (
    _THINKING_BUDGET_TOKENS,
    AUTO_REASONING_EFFORT,
    SUMMARY_MAX_TOKENS,
    resolve_thinking,
    summary_model_settings,
    thinking_model_settings,
)
from hivegent.types import LlmConfig

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


def test_resolve_thinking_maps_sentinels_and_passes_levels_through() -> None:
    assert resolve_thinking("auto") == AUTO_REASONING_EFFORT
    assert resolve_thinking("none") is False
    assert resolve_thinking("minimal") == "minimal"
    assert resolve_thinking("xhigh") == "xhigh"


def test_qwen38_maps_native_effort_without_losing_granular_budgets() -> None:
    low = thinking_model_settings("minimal", QWEN38)
    high = thinking_model_settings("high", QWEN38)

    assert low.get("thinking") == "low"
    assert low.get("extra_body") == {
        "chat_template_kwargs": {"enable_thinking": True},
        "thinking_token_budget": _THINKING_BUDGET_TOKENS["minimal"],
    }
    assert high.get("thinking") == "medium"
    assert high.get("extra_body") == {
        "chat_template_kwargs": {"enable_thinking": True},
        "thinking_token_budget": _THINKING_BUDGET_TOKENS["high"],
    }


def test_other_self_hosted_models_keep_generic_high_effort() -> None:
    settings = thinking_model_settings("high", SELF_HOSTED)

    assert settings.get("thinking") == "high"
    assert settings.get("extra_body") == {
        "chat_template_kwargs": {"enable_thinking": True},
        "thinking_budget_tokens": _THINKING_BUDGET_TOKENS["high"],
    }


def test_numeric_level_caps_reasoning_on_self_hosted() -> None:
    settings = thinking_model_settings("medium", SELF_HOSTED)

    assert settings.get("thinking") == "medium"
    assert settings.get("extra_body") == {
        "chat_template_kwargs": {"enable_thinking": True},
        "thinking_budget_tokens": _THINKING_BUDGET_TOKENS["medium"],
    }


def test_xhigh_enables_thinking_without_a_budget() -> None:
    extra_body = thinking_model_settings("xhigh", SELF_HOSTED).get("extra_body")

    assert extra_body == {"chat_template_kwargs": {"enable_thinking": True}}


def test_none_disables_thinking_without_a_budget() -> None:
    extra_body = thinking_model_settings(False, SELF_HOSTED).get("extra_body")

    assert extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_auto_omits_thinking_fields_entirely() -> None:
    settings = thinking_model_settings(None, SELF_HOSTED)

    assert "thinking" not in settings
    assert "extra_body" not in settings


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
