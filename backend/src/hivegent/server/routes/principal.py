"""The route naming the caller, which a service client checks at startup."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from ...auth import CurrentPrincipal

__all__ = ["PrincipalView", "router"]

router = APIRouter()


class PrincipalView(BaseModel):
    """Who a token authenticates as."""

    id: str
    kind: Literal["person", "service"]


@router.get("/principal")
async def get_principal(principal: CurrentPrincipal) -> PrincipalView:
    """Name the caller and its kind, so the Teams bridge refuses to run as a person."""
    return PrincipalView(id=principal.id, kind=principal.kind)
