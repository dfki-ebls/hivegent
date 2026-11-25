"""The request spans of FastAPI carry no content unless it is opted in."""

from starlette.requests import Request

from hivegent.observability import _without_arguments


def test_request_spans_drop_the_endpoint_arguments() -> None:
    """Chat bodies, the caller, and the input of a failed validation never reach an exporter."""
    attributes = {
        "values": {"body": {"messages": ["secret"]}, "user": "alice"},
        "errors": [
            {
                "type": "missing",
                "loc": ("body", "id"),
                "input": {"messages": ["secret"]},
            }
        ],
    }

    assert _without_arguments(Request({"type": "http"}), attributes) == {
        "errors": [{"type": "missing", "loc": ("body", "id")}]
    }
