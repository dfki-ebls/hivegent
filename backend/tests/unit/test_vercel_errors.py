"""Tests for canonical chat-stream error codes."""

from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior

from hivegent.server.vercel import CONTEXT_LENGTH_EXCEEDED, chat_error_text


def _is_overflow(error: Exception) -> bool:
    return chat_error_text(error).startswith(f"{CONTEXT_LENGTH_EXCEEDED}: ")


def test_openai_structured_code() -> None:
    error = ModelHTTPError(
        status_code=400,
        model_name="gpt-5.2",
        body={"code": "context_length_exceeded", "message": "too long"},
    )
    assert _is_overflow(error)


def test_llamacpp_structured_type() -> None:
    error = ModelHTTPError(
        status_code=400,
        model_name="qwen3.6-27b",
        body={
            "code": 400,
            "message": (
                "the request exceeds the available context size. try increasing "
                "the context size or enable context shift"
            ),
            "type": "exceed_context_size_error",
        },
    )
    assert _is_overflow(error)


def test_vllm_prose_body() -> None:
    error = ModelHTTPError(
        status_code=400,
        model_name="gemma-4",
        body={
            "object": "error",
            "message": "This model's maximum context length is 8192 tokens.",
            "type": "BadRequestError",
        },
    )
    assert _is_overflow(error)


def test_sglang_prose_body() -> None:
    # SGLang's body is flat (no nested `error` object), with a numeric
    # code and a generic type — only the message names the overflow.
    error = ModelHTTPError(
        status_code=400,
        model_name="qwen3.5-27b",
        body={
            "object": "error",
            "message": (
                "The input (5000 tokens) is longer than the model's "
                "context length (4096 tokens)."
            ),
            "type": "BadRequest",
            "param": None,
            "code": 400,
        },
    )
    assert _is_overflow(error)


def test_finish_reason_length_without_output() -> None:
    error = UnexpectedModelBehavior(
        "Model token limit (provider default) exceeded before any response was"
        " generated. Increase the `max_tokens` model setting, or simplify the"
        " prompt to result in a shorter response that will fit within the limit."
    )
    assert _is_overflow(error)


def test_unrelated_errors_pass_through() -> None:
    assert chat_error_text(ValueError("boom")) == "boom"
    assert not _is_overflow(
        ModelHTTPError(status_code=400, model_name="m", body={"message": "bad param"})
    )
    assert not _is_overflow(UnexpectedModelBehavior("invalid tool call"))
