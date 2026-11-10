"""RFC 9457 problem details, the one shape every API error takes.

Every refusal answers ``application/problem+json``: an ``HTTPException`` and a
validation failure alike, whichever layer raised it.  ``title`` is the status
phrase unless a :class:`ProblemException` names a problem type of its own,
``detail`` the localized explanation a client shows, and ``errors`` lists the
invalid members of a request it rejected.
"""

from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError
from pydantic.json_schema import models_json_schema
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response

from ..l10n import Localized
from ..store import AccessDenied

__all__ = [
    "PROBLEM_MEDIA_TYPE",
    "PROBLEM_RESPONSES",
    "ProblemException",
    "install_problem_handlers",
    "problem_response",
]

PROBLEM_MEDIA_TYPE = "application/problem+json"

_INTERNAL_ERROR = Localized(
    en="The server failed to answer the request.",
    de="Der Server konnte die Anfrage nicht beantworten.",
)
_INVALID_REQUEST = Localized(en="Invalid request", de="Ungültige Anfrage")


def _invalid_data(title: str) -> Localized[str]:
    return Localized(en=f"Invalid data for {title}", de=f"Ungültige Daten für {title}")


class ProblemError(BaseModel):
    """One invalid member of a rejected request."""

    detail: str = Field(description="What is wrong with the member")
    pointer: str | None = Field(
        default=None,
        description="JSON Pointer to the member within the body, such as `#/messages/0`",
    )
    parameter: str | None = Field(
        default=None, description="The path, query, or header parameter"
    )


class Problem(BaseModel):
    """An RFC 9457 problem detail, the body of every error response."""

    type: str = Field(
        default="about:blank",
        description="URI of the problem type, `about:blank` when the status says it all",
    )
    title: str = Field(description="Short summary of the problem type")
    status: int = Field(description="The HTTP status code")
    detail: str | None = Field(
        default=None, description="Explanation of this occurrence, localized"
    )
    errors: list[ProblemError] | None = Field(
        default=None, description="The invalid members of a rejected request"
    )


class ProblemException(HTTPException):
    """An HTTP error of a problem type beyond what its status says."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        type: str,
        title: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(status_code, detail, headers)
        self.type = type
        self.title = title


def problem_response(description: str) -> dict[str, Any]:
    """An OpenAPI response entry for a problem answered for *description*."""
    return {
        "description": description,
        "content": {
            PROBLEM_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/Problem"}}
        },
    }


PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    # Replaces FastAPI's own 422 entry, whose body this API never sends.
    422: problem_response("The request is invalid"),
    "default": problem_response("Any other refusal or failure"),
}
"""The error responses every route documents."""


def _segment(part: int | str) -> str:
    return str(part).replace("~", "~0").replace("/", "~1")


def _member(loc: Sequence[int | str], detail: str) -> ProblemError:
    """The member at *loc*, which FastAPI leads with where in the request it is."""
    match loc:
        case ("body", *path):
            return ProblemError(
                detail=detail, pointer="#/" + "/".join(map(_segment, path))
            )
        case (_, *path) if path:
            return ProblemError(
                detail=detail, parameter=".".join(str(part) for part in path)
            )
        case _:
            return ProblemError(detail=detail)


def _members(errors: Sequence[Any], prefix: tuple[str, ...] = ()) -> list[ProblemError]:
    """*errors* as problem members, each located by *prefix* and its own ``loc``.

    A validator's own ``ValueError`` is already localized, unlike pydantic's
    messages, so it is shown without pydantic's English "Value error" prefix.
    """
    return [
        _member(
            (*prefix, *error["loc"]),
            str(error.get("ctx", {}).get("error", error["msg"])),
        )
        for error in errors
    ]


def _problem(
    status: int,
    detail: str | None,
    *,
    type: str = "about:blank",
    title: str | None = None,
    errors: list[ProblemError] | None = None,
    headers: Mapping[str, str] | None = None,
) -> Response:
    problem = Problem(
        type=type,
        title=title or HTTPStatus(status).phrase,
        status=status,
        detail=detail,
        errors=errors,
    )

    return JSONResponse(
        problem.model_dump(mode="json", exclude_none=True),
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


async def _handle(_request: Request, exc: Exception) -> Response:
    """Answer *exc* as a problem, whichever layer raised it."""
    match exc:
        case ProblemException():
            return _problem(
                exc.status_code,
                exc.detail,
                type=exc.type,
                title=exc.title,
                headers=exc.headers,
            )
        case RequestValidationError():
            return _problem(
                422,
                _INVALID_REQUEST.current,
                errors=_members(exc.errors()),
            )
        case ValidationError():
            return _problem(
                422,
                _invalid_data(exc.title).current,
                errors=_members(exc.errors(), ("body",)),
            )
        case HTTPException():
            return _problem(exc.status_code, exc.detail, headers=exc.headers)
        case AccessDenied():
            return _problem(403, str(exc))
        case _:
            # Starlette raises it on after the answer, so the server logs it.
            return _problem(500, _INTERNAL_ERROR.current)


def install_problem_handlers(app: FastAPI) -> None:
    """Answer every error *app* raises as a problem, and document them so."""
    for kind in (
        HTTPException,
        RequestValidationError,
        ValidationError,
        AccessDenied,
        Exception,
    ):
        app.add_exception_handler(kind, _handle)

    generate = app.openapi

    # No route returns a `Problem`, so its schema joins the components here,
    # where the `$ref` of :data:`PROBLEM_RESPONSES` points.
    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = generate()
            _, problems = models_json_schema(
                [(Problem, "serialization")],
                ref_template="#/components/schemas/{model}",
            )
            schema.setdefault("components", {}).setdefault("schemas", {}).update(
                problems["$defs"]
            )
            app.openapi_schema = schema

        return app.openapi_schema

    app.openapi = openapi
