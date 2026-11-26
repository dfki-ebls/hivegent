"""Routes running a chat turn and following or stopping it."""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from functools import partial
from itertools import chain
from typing import Any
from uuid import uuid4

from exar.pydantic_ai import traced_events
from fastapi import APIRouter, HTTPException, status
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.conversation import Conversation as RunConversation
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.ui import SSE_CONTENT_TYPE
from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk
from pydantic_ai.usage import RunUsage
from pydantic_ai_harness.sub_agents import DelegationReports
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from ....agents import (
    RunPrefix,
    chat_agent,
    record_known_urls,
    retain_awaited_changes,
    turn_usage_limits,
)
from ....agents.compaction import compact_on_overflow
from ....auth import (
    CurrentPrincipal,
    refuse_expired,
)
from ....config import parse_uuid, settings
from ....db.conversations import (
    OwnedConversation,
    claim_conversation,
    extract_title,
    is_user_request,
    load_conversation,
    render_prompt,
)
from ....delegations import delegations
from ....llm import thinking_model_settings
from ....llm_config import background_llm
from ....runs import Prepared, Run, runs
from ....shared.l10n import Localized, weighted_ranges
from ....store import User
from ....tmp import open_tmp
from ....types import ChatRequest, DocumentSelection
from ...common import CONVERSATION_NOT_FOUND, build_run_prefix, effective_preferences
from ...problems import problem_response
from ...rate_limit import CHAT, RESUME
from ...turns import (
    Replay,
    abandon_pending_calls,
    kept_before,
    stamp,
    stored_turn,
    ui_id,
)
from ...vercel import (
    SDK_VERSION,
    UI_MESSAGES,
    ChatAdapter,
    RunEnd,
    StaleAnswers,
    agent_events,
    run_and_persist,
    stream_response,
)
from .attachments import accept_attachments
from .compaction import NOTHING_TO_COMPACT, SavedTurn, compact_history, save_turn
from .deps import TURN_RUNNING, Adapter, Opened
from .history import active_messages
from .titles import title_first_turn

__all__ = ["chat_router", "router"]

logger = logging.getLogger(__name__)
router = APIRouter()

chat_router = APIRouter()
"""The one route a service principal may reach, mounted outside the people-only routes."""


_INVALID_CHAT_REQUEST = Localized(en="Invalid chat request", de="Ungültige Chatanfrage")


_JSON = "application/json"

_STREAM = {
    SSE_CONTENT_TYPE: {
        "schema": {
            "type": "string",
            "description": (
                "A Vercel AI UI message stream, "
                "see https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol"
            ),
        }
    }
}

_CHAT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": (
            "The turn as it streams, or with `Accept: application/json` the "
            "conversation's active history once the turn is stored, its last message "
            "being the answer"
        ),
        "content": {
            **_STREAM,
            _JSON: {
                "schema": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/UIMessage"},
                }
            },
        },
    },
    409: problem_response(
        "Another turn of the conversation is running, or the request answers "
        "calls the conversation no longer awaits"
    ),
    502: problem_response("The turn failed, with `Accept: application/json`"),
}

_RESUME_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"description": "The running turn, from its first chunk", "content": _STREAM},
    204: {"description": "No turn is running"},
}

_INVALID_MESSAGE_ID = Localized(
    en="A new message needs a UUID as its id",
    de="Eine neue Nachricht braucht eine UUID als ID",
)

_MESSAGE_ID_TAKEN = Localized(
    en="A message with this id exists already, send it under a new id",
    de="Eine Nachricht mit dieser ID existiert bereits, sende sie unter einer neuen ID",
)

_AWAITS_ANSWERS = Localized(
    en="The conversation awaits approvals or answers",
    de="Die Konversation wartet auf Freigaben oder Antworten",
)

_CONVERSATION_CHANGED = Localized(
    en="The conversation changed since it was loaded, reload it and answer again",
    de="Die Konversation hat sich seit dem Laden geändert, lade sie neu und antworte erneut",
)


async def _claimed(
    conversation: OwnedConversation, title: str | None
) -> RunConversation:
    """*conversation* claimed under *title*, or its stored state if a claim raced.

    Raises:
        HTTPException: 404 when another user claimed its id since it was opened.
    """
    if (claimed := await claim_conversation(conversation, title=title)) is None:
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND.current)

    return claimed


def _resolve_history(
    stored: Sequence[ModelMessage],
    pending: DeferredToolRequests | None,
    config: ChatRequest,
    prompt: UIMessage | None,
) -> tuple[list[ModelMessage], DeferredToolRequests | None, str]:
    """The history *stored* gives the turn, the calls it awaits from *pending*, and the id of the reply.

    Raises:
        HTTPException: 409 when the prompt's id is taken in the history.
    """
    regenerate = config.trigger == "regenerate-message"
    history = list(stored)

    # The client continues its last reply unless the request opens a new one.
    continues = (
        prompt is None
        and not regenerate
        and bool(history)
        and not is_user_request(history[-1])
    )
    reply_id = (continues and ui_id(history[-1])) or str(uuid4())
    target = None if config.message_id is None else str(config.message_id)

    if regenerate or (prompt is not None and target is not None):
        kept = kept_before(history, target, regenerate=regenerate, reply_id=reply_id)

        if kept is not None and len(kept) < len(history):
            history, pending = kept, None

    # Checked before the turn runs or saves, so a client resending a
    # message the history holds learns it before the turn would store it twice.
    if prompt is not None and prompt.id in {ui_id(message) for message in history}:
        raise HTTPException(status_code=409, detail=_MESSAGE_ID_TAKEN.current)

    return history, pending, reply_id


@asynccontextmanager
async def _delegating(
    conversation: OwnedConversation, user: User
) -> AsyncGenerator[list[DelegationReports]]:
    """Bind the conversation's task owner while the turn runs, with what delivers the reports of its tasks.

    The tasks the turn delegates run in the background past it, and a report
    reaches the turn at its next model request, or wakes the conversation
    once no turn takes it (:func:`_wake`).
    A service, such as the Teams bridge, reads only the turn's own stream, so
    its turn binds no owner and the harness waits for its tasks within it.
    """
    if user.is_service or not settings.subagents.enabled:
        yield []
        return

    async with delegations.turn(conversation, user, _wake) as owner:
        with owner.bind():
            yield [
                DelegationReports(
                    owner, conversation_id=conversation.key, priority="asap"
                )
            ]


async def _stream(
    conversation: OwnedConversation,
    user: User,
    adapter: Adapter,
    run_prefix: RunPrefix,
    history: list[ModelMessage],
    deferred: DeferredToolResults | None,
    usage: RunUsage,
    end: RunEnd,
) -> AsyncGenerator[BaseChunk]:
    """The stream of the turn, the root invocation `chat` of an exar run, which only traces."""
    async with _delegating(conversation, user) as reports:
        async for chunk in adapter.transform_stream(
            traced_events(
                agent_events(
                    chat_agent.run_stream_events(
                        adapter.prompt if deferred is None else None,
                        message_history=history,
                        deferred_tool_results=deferred,
                        **run_prefix.run_kwargs(),
                        model_settings=thinking_model_settings(run_prefix.llm),
                        usage_limits=turn_usage_limits,
                        usage=usage,
                        capabilities=[
                            *run_prefix.capabilities,
                            compact_on_overflow(run_prefix),
                            *reports,
                        ],
                    ),
                    end,
                ),
                ""
                if adapter.prompt is None
                else render_prompt(UserPromptPart(adapter.prompt)),
                run_prefix.deps.run_config,
                name="chat",
            )
        ):
            yield chunk


_wakers: dict[OwnedConversation, asyncio.Task[None]] = {}
"""The wake-ups waiting for their conversation's slot, one per conversation."""


def _wake(conversation: OwnedConversation, user: User) -> None:
    """Start a turn without a prompt for the reports of *conversation*'s tasks, once no turn runs."""
    if conversation in _wakers:
        return

    waker = _wakers[conversation] = asyncio.create_task(_wake_up(conversation, user))
    waker.add_done_callback(lambda _: _wakers.pop(conversation, None))


async def _wake_up(conversation: OwnedConversation, user: User) -> None:
    """Wait for the conversation's slot, then read the reports no turn took in a turn of its own.

    The turn continues the last reply, as a post-approval continuation does,
    and its stream is followed like any (``GET .../stream``), which the
    clients of the conversation learn of on their feed.  It gives way to a
    request of the user, whose turn then reads the reports it did not.
    """
    while (run := runs.get(conversation)) is not None:
        await run.wait()

    if delegations.pending(conversation):
        config = ChatRequest(id=conversation.key, messages=[])
        adapter = ChatAdapter(
            agent=chat_agent,
            run_input=config.run_input(),
            accept=SSE_CONTENT_TYPE,
            sdk_version=SDK_VERSION,
        )
        _ = runs.start(
            conversation,
            user,
            None,
            _prepare_turn(user, conversation, config, adapter, None, woken=True),
            preemptible=True,
        )


def _persister(
    conversation: OwnedConversation,
    loaded: RunConversation,
    *,
    replay: Replay,
    kept: list[ModelMessage],
    declined: ModelRequest | None,
    prompt_id: str | None,
    reply_id: str,
    usage: RunUsage,
    end: RunEnd,
    saved: SavedTurn,
) -> Callable[[Sequence[ModelMessage]], Awaitable[None]]:
    """What saves the turn on top of the *kept* history once it ended.

    Its usage includes what the tasks of earlier turns spent since those were
    stored (:meth:`~hivegent.delegations.Delegations.usage_for_save`).
    """
    started = time.time()

    async def persist(messages: Sequence[ModelMessage]) -> None:
        turn = replay.turn(messages)
        spent, stored = delegations.usage_for_save(conversation, usage)
        loaded.usage += spent

        loaded.deferred_tool_requests = end.deferred
        await save_turn(
            conversation,
            loaded,
            stored_turn(turn, kept, declined, prompt_id=prompt_id, reply_id=reply_id),
            saved,
        )
        stored()
        await retain_awaited_changes(
            conversation, loaded.deferred_tool_requests, started
        )

    return persist


async def _prepare_turn(
    user: User,
    conversation: OwnedConversation,
    config: ChatRequest,
    adapter: Adapter,
    prompt: UIMessage | None,
    *,
    woken: bool = False,
) -> Prepared:
    """The turn *config* asks for, every check that can refuse it passed.

    The history is the stored conversation's, loaded from the store, never
    the browser's echo, which sends only its new message (none for a
    regenerate or a compaction).  An edit or a regenerate runs on the history
    that shows before the message it replaces (:func:`.turns.kept_before`),
    which the store keeps beside the new turn as a branch.

    A turn's new user message keeps the id the client gave it (:func:`_prompt`),
    and a turn that streams a new reply rather than continuing the last one
    announces the id it stores the reply under, so the client streams it under
    the id a reload shows.  A request flagged ``compact`` compacts the history
    instead of running a turn.  An absent conversation is claimed before
    deriving replay state, titled by its prompt so it is listed while the
    turn runs, and by the aux model once it ended (:func:`.titles.title_first_turn`).
    A racing claim returns the stored state to replay instead.

    A turn *woken* by the reports of the conversation's tasks (:func:`_wake`)
    spends no request of the user's, and is refused while the conversation
    awaits approvals or answers, which it would abandon, so the turn that
    answers them reads the reports.
    """
    # Spent before any work, a continuation once the stored turn tells whether it answers anything.
    resumes = _resumes(config, adapter)

    if not resumes and not woken:
        CHAT.spend(user)

    loaded, effective = await asyncio.gather(
        load_conversation(conversation), effective_preferences(user)
    )

    if loaded is None and config.compact:
        raise HTTPException(status_code=400, detail=NOTHING_TO_COMPACT.current)

    kept, pending, reply_id = (
        _resolve_history((), None, config, prompt)
        if loaded is None
        else _resolve_history(
            loaded.messages, loaded.deferred_tool_requests, config, prompt
        )
    )
    # The documents of the question the turn answers, the new one's or the one a
    # regenerate, a resumed turn, or a compaction answers again.
    run_prefix = await build_run_prefix(
        DocumentSelection.of([*kept, *adapter.messages]), effective, user, conversation
    )

    # Gate the new user message's attachments before the run. Sanitisation
    # lands in place, and the adapter caches `messages`, so `run_stream` and
    # persistence both see the admitted bytes.
    await accept_attachments(adapter.messages, llm=run_prefix.llm)

    saved = SavedTurn()
    after: Callable[[], Awaitable[None]] | None = None

    if loaded is None:
        # Resolve a racing claim, whose stored state the turn then runs on.
        claimed = extract_title(adapter.messages)
        loaded = await _claimed(conversation, claimed)
        kept, pending, reply_id = _resolve_history(
            loaded.messages, loaded.deferred_tool_requests, config, prompt
        )

        if (aux := background_llm()) is not None:
            after = partial(title_first_turn, conversation, saved, aux, claimed)

    adapter.server_message_id = reply_id

    if woken and pending is not None:
        raise HTTPException(status_code=409, detail=_AWAITS_ANSWERS.current)

    # Ahead of the approvals and answers below, which a compaction leaves pending.
    if config.compact:
        return Prepared(
            compact_history(
                conversation, adapter, run_prefix, loaded, kept, reply_id, saved
            ),
            kept,
            lambda: saved.snapshot,
        )

    # The client's decisions and answers count only for the calls the stored
    # conversation still awaits, each with the kind of result it was deferred for.
    try:
        deferred = None if woken else adapter.resume(pending, kept)
    except StaleAnswers as exc:
        raise HTTPException(
            status_code=409, detail=_CONVERSATION_CHANGED.current
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=_INVALID_CHAT_REQUEST.current
        ) from exc

    # Only answers to calls the turn awaits continue it, anything else runs a new one.
    if resumes:
        (CHAT if deferred is None else RESUME).spend(user)

    open_tmp(conversation)

    # A turn that ended awaiting approval or answers left its calls dangling so
    # this request could answer them.  One that carries anything else abandons
    # them, and the dangling calls have to be closed here or every later turn
    # replays them as a generic "interrupted" result and the model reissues the
    # same calls again and again.  The closing request belongs to the reply
    # that asked, and the run replays it with the history.
    declined = abandon_pending_calls(pending) if deferred is None else None
    history = kept

    if declined is not None:
        stamp([declined], ui_id(kept[-1]) or reply_id)
        history = [*kept, declined]

    record_known_urls(run_prefix.deps.web.known, chain(history, adapter.messages))
    # Shared with the subagents, so it holds what the whole turn spent however it ended.
    usage = RunUsage()
    end = RunEnd()
    stream = _stream(
        conversation, user, adapter, run_prefix, history, deferred, usage, end
    )
    persist = _persister(
        conversation,
        loaded,
        replay=Replay.of(history),
        kept=kept,
        declined=declined,
        prompt_id=None if prompt is None else prompt.id,
        reply_id=reply_id,
        usage=usage,
        end=end,
        saved=saved,
    )

    return Prepared(
        run_and_persist(adapter, stream, persist=persist),
        history,
        lambda: saved.snapshot,
        after=after,
    )


async def _answer(
    conversation: OwnedConversation,
    run: Run,
    accept: str | None,
    user: User,
) -> Response:
    """The turn as it streams, or the stored conversation once it ended for a JSON client.

    A turn its preparation refused answers with the refusal instead.
    Neither outlives the caller's token: a stream ends when it expires, and a
    JSON client whose token expired while the turn ran is refused.
    """
    if not await run.prepared() and run.refusal is not None:
        raise run.refusal

    if _quality(accept, _JSON) <= _quality(accept, SSE_CONTENT_TYPE):
        return stream_response(run.follow(user))

    await run.wait()
    refuse_expired(user.expires_at)

    if (error := run.error) is not None:
        raise HTTPException(status_code=502, detail=error)

    return Response(
        UI_MESSAGES.dump_json(await active_messages(conversation), by_alias=True),
        media_type=_JSON,
    )


def _quality(accept: str | None, media_type: str) -> float:
    """The quality *accept* gives *media_type* (RFC 9110), its most specific range deciding.

    Examples:
        >>> _quality("application/json, text/event-stream;q=0.5", "text/event-stream")
        0.5
        >>> _quality("application/jsonl, */*;q=0.1", "application/json")
        0.1
        >>> _quality(None, "application/json")
        0.0
    """
    kind = media_type.split("/")[0]
    ranges = weighted_ranges(accept)

    return next(
        (ranges[r] for r in (media_type, f"{kind}/*", "*/*") if r in ranges), 0.0
    )


def _resumes(config: ChatRequest, adapter: Adapter) -> bool:
    """Whether the request claims to answer the approvals or questions a turn awaits.

    Only a post-approval continuation does, never a prompt, a regenerate, or a
    compaction, and whether it answers any is up to the stored turn.
    """
    return (
        not config.compact
        and config.trigger != "regenerate-message"
        and _continued(adapter) is not None
    )


def _continued(adapter: Adapter) -> UIMessage | None:
    """The assistant message a post-approval continuation sends, with its decisions."""
    sent = adapter.run_input.messages[-1:]

    return sent[0] if sent and sent[0].role == "assistant" else None


def _prompt(adapter: Adapter) -> UIMessage | None:
    """The turn's new user message under its id in canonical form, ``None`` without one.

    A regenerate, a compaction, and a post-approval continuation carry no new
    user message, so the client addresses none of their nodes.

    Raises:
        HTTPException: 422 when the id is no UUID.
    """
    if not adapter.messages or not is_user_request(adapter.messages[-1]):
        return None

    message = next(
        message
        for message in reversed(adapter.run_input.messages)
        if message.role == "user"
    )
    parsed = parse_uuid(message.id)

    if parsed is None:
        raise HTTPException(status_code=422, detail=_INVALID_MESSAGE_ID.current)

    return message.model_copy(update={"id": str(parsed)})


@chat_router.post(
    "/conversations/{conversation_id}/chat",
    response_class=StreamingResponse,
    responses=_CHAT_RESPONSES,
)
async def create_conversation_chat(
    opened: Opened,
    user: CurrentPrincipal,
    config: ChatRequest,
    request: Request,
) -> Response:
    """Run a turn in a conversation, starting it at a fresh id the client chose.

    The turn runs on its own (:mod:`~hivegent.runs`), so a client that
    leaves only stops following it, a retry with the same user message follows
    the running turn, any other request is refused untouched while it runs,
    unless the conversation woke that turn itself for its tasks' reports,
    which the request stops, and ``GET .../stream`` follows it again after a reload.  Every request
    of the user renews the turn's authorization (:class:`~hivegent.auth.Lease`).
    """
    conversation, _ = opened
    accept = request.headers.get("accept")
    adapter = ChatAdapter(
        agent=chat_agent,
        run_input=config.run_input(),
        accept=accept,
        sdk_version=SDK_VERSION,
    )
    prompt = _prompt(adapter)

    # A turn woken for reports gives way, a retry of the running turn's
    # request follows it, and any other is refused.
    if (run := await runs.preempt(conversation)) is None:
        run = runs.start(
            conversation,
            user,
            prompt or _continued(adapter),
            _prepare_turn(user, conversation, config, adapter, prompt),
        )
    elif prompt is None or run.message is None or run.message.id != prompt.id:
        raise HTTPException(status_code=409, detail=TURN_RUNNING.current)

    return await _answer(conversation, run, accept, user)


@chat_router.get(
    "/conversations/{conversation_id}/stream",
    response_class=StreamingResponse,
    responses=_RESUME_RESPONSES,
)
async def follow_conversation_turn(opened: Opened, user: CurrentPrincipal) -> Response:
    """Follow the running turn of a conversation from its first chunk.

    The AI SDK's resumable stream: ``204`` while no turn runs, so a client
    reloading after the turn ended reads it from ``.../messages`` instead.
    It ends without the stored turn when the caller's token expires, which
    the client follows again with a fresh one.
    """
    run = runs.get(opened[0])

    if run is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return stream_response(run.follow(user))


@router.delete(
    "/conversations/{conversation_id}/stream",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def stop_conversation_turn(opened: Opened) -> None:
    """Stop the running turn of a conversation, once it stored what it got to."""
    await runs.cancel(opened[0])
