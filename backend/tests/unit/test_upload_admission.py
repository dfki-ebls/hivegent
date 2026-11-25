"""Unit tests for the room an upload reserves before its job exists."""

import asyncio
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile

from hivegent.jobs import JobManager
from hivegent.server.routes import documents
from hivegent.shared.l10n import Localized
from hivegent.store import User


async def test_parallel_uploads_cannot_all_pass_the_job_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = JobManager(max_owner_jobs=1)
    monkeypatch.setattr(documents, "manager", manager)
    user = User("u1")

    async def upload() -> None:
        file = UploadFile(BytesIO(b"x"), size=1)

        async with documents._spooled(  # pyright: ignore[reportPrivateUsage]
            file, user, limit=10, too_large=lambda _: Localized(en="", de="")
        ):
            await asyncio.sleep(0)

    results = await asyncio.gather(upload(), upload(), return_exceptions=True)
    failures = [result for result in results if isinstance(result, HTTPException)]

    assert [failure.status_code for failure in failures] == [429]
    assert manager.admits(user.id, 0)
