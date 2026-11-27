"""Unit tests for the room an upload reserves before its job exists."""

import asyncio
from contextlib import AbstractAsyncContextManager
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from hivegent.jobs import JobManager
from hivegent.server.routes import documents
from hivegent.shared.l10n import Localized
from hivegent.store import User


async def test_an_upload_in_flight_holds_its_room_until_its_block_ends() -> None:
    manager = JobManager(max_owner_jobs=1)
    user = User("u1")
    entered, leave = asyncio.Event(), asyncio.Event()

    def spooled() -> AbstractAsyncContextManager[Path]:
        return documents._spooled(  # pyright: ignore[reportPrivateUsage]
            UploadFile(BytesIO(b"x"), size=1),
            user,
            limit=10,
            too_large=lambda _: Localized(en="", de=""),
            jobs=manager,
        )

    async def first() -> None:
        async with spooled():
            entered.set()
            await leave.wait()

    task = asyncio.create_task(first())
    await entered.wait()

    with pytest.raises(HTTPException) as refused:
        async with spooled():
            pass

    assert refused.value.status_code == 429

    leave.set()
    await task

    assert manager.admits(user.id, 0)
