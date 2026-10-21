"""Routes for conversations and chat orchestration."""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import replace
from itertools import chain
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from pydantic_ai import BinaryContent, DeferredToolRequests
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartStartEvent,
    TextPart,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.ui import NativeEvent
from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from starlette.requests import Request
from starlette.responses import Response

from ... import conversation_files
from ...agents import (
    RunPrefix,
    UserDeps,
    discard_unapproved_changes,
    record_known_urls,
    title_agent,
    turn_usage_limits,
    user_agent,
)
from ...agents.compaction import (
    CompactOnOverflow,
    compact,
    compaction_cut,
    is_compaction,
)
from ...agents.subagent_events import SubagentUpdate
from ...auth import get_current_user
from ...config import settings
from ...converters import INGESTIBLE_IMAGE_MEDIA_TYPES
from ...converters.images import sanitize_image_bytes
from ...db._common import new_id
from ...db.conversations import (
    UNTITLED,
    ConversationSummary,
    MessagePair,
    OwnedConversation,
    append_branch,
    import_conversation,
    insert_above,
    is_user_request,
    list_conversations,
    load_active_for_display,
    load_conversation,
    load_conversation_summary,
    owned_conversation,
    prompt_items,
    prompt_text,
    resolve_fork,
    set_conversation_title,
)
from ...humanize import format_bytes, pluralize
from ...l10n import Localized
from ...llm import (
    model_from_config,
    thinking_model_settings,
)
from ...store import User
from ...tools.formatting import BLOCK_SEP
from ...transparency import sign_provenance
from ...types import (
    ChatRequestConfig,
    ConversationArchive,
    ConversationListResponse,
    GenerateTitleRequest,
    GenerateTitleResponse,
    InstructionsSnapshot,
    ServerConversation,
    UpdateTitleRequest,
)
from ..cancellation import run_until_disconnect
from ..common import build_run_prefix, prepare_llm_config
from ..operations import attachment_disposition
from ..vercel import (
    SDK_VERSION,
    ChatAdapter,
    abandon_pending_calls,
    dump_messages_with_ids,
    run_and_persist,
)

__all__ = ["router"]

logger = logging.getLogger(__name__)
router = APIRouter()

_CONVERSATION_NOT_FOUND = Localized(
    en="Conversation not found", de="Konversation nicht gefunden"
)


def _found[T](value: T | None) -> T:
    """*value*, or ``404`` for a conversation that is missing or not the caller's."""
    if value is None:
        raise HTTPException(status_code=404, detail=_CONVERSATION_NOT_FOUND.current)

    return value


_TITLE_INSTRUCTIONS = Localized(
    en="""Generate a short, descriptive title (max 60 characters) for the conversation.
The title should capture the main topic or question.
Write the title in the language of the conversation, or in English if that is unclear.
Return ONLY the title, no quotes or extra text.
""",
    de="""Erstelle einen kurzen, aussagekräftigen Titel (maximal 60 Zeichen) für die Konversation.
Der Titel soll das Hauptthema oder die Hauptfrage erfassen.
Schreibe den Titel in der Sprache der Konversation oder auf Deutsch, wenn diese unklar ist.
Gib NUR den Titel zurück, ohne Anführungszeichen oder zusätzlichen Text.
""",
)


def _title_prompt(preview: str) -> Localized[str]:
    return Localized(en=f"Conversation:\n{preview}", de=f"Konversation:\n{preview}")


_TITLE_FAILED = Localized(
    en="Failed to generate title", de="Titel konnte nicht erstellt werden"
)


_NOTHING_TO_COMPACT = Localized(
    en="There is nothing to compact: the conversation has no answered turn since its last compaction, or a request still awaits an answer.",
    de="Es gibt nichts zu komprimieren: Seit der letzten Komprimierung hat die Konversation keine beantwortete Runde, oder eine Anfrage wartet noch auf eine Antwort.",
)
_ONLY_IMAGES = Localized(
    en=(
        "Only images can be attached to a chat message. Upload other "
        "documents to your workspace, where they are converted and "
        "indexed for the assistant to search."
    ),
    de=(
        "An eine Chatnachricht können nur Bilder angehängt werden. Lade andere "
        "Dokumente in deinen Arbeitsbereich hoch, wo sie konvertiert und für die "
        "Suche durch den Assistenten indexiert werden."
    ),
)


def _image_too_large(limit: int) -> Localized[str]:
    return Localized(
        en=f"Attached image exceeds the {format_bytes(limit)} limit.",
        de=f"Das angehängte Bild überschreitet die Grenze von {format_bytes(limit)}.",
    )


def _too_many_images(cap: int) -> Localized[str]:
    return Localized(
        en=(
            f"At most {cap} {pluralize(cap, 'image')} can be attached to a message, "
            "since the model server accepts no more in one request."
        ),
        de=(
            f"An eine Nachricht {pluralize(cap, 'kann', 'können')} höchstens {cap} "
            f"{pluralize(cap, 'Bild', 'Bilder')} angehängt werden, da der "
            "Modellserver nicht mehr pro Anfrage annimmt."
        ),
    )


_INVALID_CHAT_REQUEST = Localized(en="Invalid chat request", de="Ungültige Chatanfrage")


async def _owned_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> OwnedConversation:
    """The path's conversation, once the database names the caller its owner.

    Every ``{conversation_id}`` route takes the :class:`OwnedConversation` its
    message tree and ``/tmp`` need from here.
    """
    return _found(await owned_conversation(user, conversation_id))


type Owned = Annotated[OwnedConversation, Depends(_owned_conversation)]


@router.get("/conversations")
async def get_conversations(
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationListResponse:
    """List all conversations with summary information."""
    return ConversationListResponse(conversations=await list_conversations(user))


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation: Owned) -> ConversationSummary:
    """Get summary information for a specific conversation."""
    return _found(await load_conversation_summary(conversation))


@router.patch("/conversations/{conversation_id}")
async def update_conversation_title(
    conversation: Owned, request: UpdateTitleRequest
) -> ConversationSummary:
    """Update the title of a conversation."""
    return _found(await set_conversation_title(conversation, request.title))


def _extract_message_texts(
    messages: Sequence[ModelMessage],
    max_messages: int = 4,
) -> list[str]:
    """Extract text content from conversation messages."""
    texts: list[str] = []
    for message in messages:
        for part in message.parts:
            if (
                isinstance(part, (UserPromptPart, TextPart))
                and isinstance(part.content, str)
                and (text := part.content.strip())
            ):
                texts.append(text[:500])
                if len(texts) >= max_messages:
                    return texts
    return texts


@router.post("/conversations/{conversation_id}/title/generation")
async def generate_conversation_title(
    conversation: Owned,
    request: GenerateTitleRequest,
    http_request: Request,
) -> GenerateTitleResponse:
    """Generate a title for a conversation using an LLM."""
    data = _found(await load_conversation(conversation))

    messages_text = _extract_message_texts(data.messages)
    if not messages_text:
        return GenerateTitleResponse(title=data.title or UNTITLED.current)

    conversation_preview = BLOCK_SEP.join(messages_text)

    async def _generate() -> str:
        resolved = prepare_llm_config(request.llm)
        result = await title_agent.run(
            _title_prompt(conversation_preview).current,
            model=model_from_config(resolved),
            model_settings=thinking_model_settings(False, resolved),
            instructions=_TITLE_INSTRUCTIONS.current,
        )
        return result.output

    try:
        output = await run_until_disconnect(http_request, _generate())
        generated_title = output.strip().strip("\"'")
        if len(generated_title) > 100:
            generated_title = generated_title[:97] + "..."

        await set_conversation_title(conversation, generated_title)
        return GenerateTitleResponse(title=generated_title)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to generate title for conversation %s", conversation.id
        )
        raise HTTPException(status_code=500, detail=_TITLE_FAILED.current) from exc


@router.delete(
    "/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_conversation(conversation: Owned) -> None:
    """Delete a conversation and its files on disk."""
    if not await conversation_files.delete_conversation(conversation):
        raise HTTPException(status_code=404, detail=_CONVERSATION_NOT_FOUND.current)


@router.delete("/conversations", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_conversations_route(
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Delete all conversations for the authenticated user, and their files on disk."""
    await conversation_files.delete_conversations(user)


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(conversation: Owned) -> list[UIMessage]:
    """Get the active path of a conversation in Vercel AI format.

    Each ``UIMessage.id`` is the tree-node id the client addresses for edit /
    regenerate; forking nodes carry branch metadata.
    """
    result = await load_active_for_display(conversation)
    if result is None:
        return []
    pairs, siblings = result
    return dump_messages_with_ids(pairs, siblings=siblings)


def _instruction_snapshots(pairs: Sequence[MessagePair]) -> list[InstructionsSnapshot]:
    """Group *pairs* by the system prompt each message was sent under.

    pydantic-ai stamps the fully composed prompt (agent-level blocks plus every
    live capability's) onto each
    ``ModelRequest`` it sends, and that survives into the stored history, so the
    prompts are read back here rather than recomposed — recomposing would
    describe today's settings, not the ones the turn actually ran under.
    Consecutive messages sharing a prompt collapse into one snapshot.
    """
    snapshots: list[InstructionsSnapshot] = []

    for node_id, message in pairs:
        if not isinstance(message, ModelRequest) or not message.instructions:
            continue

        text = message.instructions

        if snapshots and snapshots[-1].text == text:
            snapshots[-1].message_ids.append(node_id)
        else:
            snapshots.append(InstructionsSnapshot(message_ids=[node_id], text=text))

    return snapshots


@router.get("/conversations/{conversation_id}/export")
async def export_conversation_route(conversation: Owned) -> Response:
    """Export a conversation's active path as a downloadable archive.

    The payload is a :class:`ConversationArchive` whose ``backend`` half carries
    the persisted messages and the system prompts they ran under; ``frontend``
    is left unset, since this route has no browser state to speak for.  The
    export button fills that half in and downloads the same shape, and the
    counterpart :func:`import_conversation_route` restores either one.
    """
    summary = _found(await load_conversation_summary(conversation))

    result = await load_active_for_display(conversation)
    pairs, siblings = result if result else ([], {})
    server = ServerConversation(
        id=summary.id,
        title=summary.title,
        messages=dump_messages_with_ids(pairs, siblings=siblings),
        instructions=_instruction_snapshots(pairs),
    )

    # Signed metadata is the machine-readable marking a container format
    # carries (Code of Practice, Sub-measure 1.1.1).
    if settings.transparency is not None:
        server.provenance = sign_provenance(settings.transparency, server)

    archive = ConversationArchive(backend=server)
    return Response(
        content=archive.model_dump_json(indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": attachment_disposition(
                f"conversation-{conversation.id}.json"
            )
        },
    )


@router.post("/conversations/import")
async def import_conversation_route(
    archive: ConversationArchive,
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationSummary:
    """Restore an exported archive as a new conversation owned by the user.

    The persisted ``backend`` half is restored when it has messages, falling
    back to ``frontend`` so an archive taken of a draft, or of a turn that
    errored before reaching the database, still imports.  The UI messages are
    decoded to model messages and stored under fresh ids as a single linear
    branch; the archive's system prompts are a record of the original run and
    are not replayed, since the imported conversation runs under the importing
    user's own settings.  Documents referenced by embedded tool outputs that do
    not exist for this user are left unresolved rather than raising, so a
    conversation captured against a different document collection still imports
    cleanly.
    """
    ui_messages, title = archive.active_path()
    try:
        messages = ChatAdapter.load_messages(ui_messages)
        return await import_conversation(user, messages, title=title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _attached_items(messages: Sequence[ModelMessage]) -> Iterator[UserContent]:
    """The non-text items of every user prompt in *messages*, in order."""
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue

        for part in message.parts:
            if isinstance(part, UserPromptPart):
                yield from (i for i in prompt_items(part) if prompt_text(i) is None)


def _accept_attachment(item: UserContent) -> None:
    """Admit one attached item, sanitising it in place, or reject it.

    Raises :class:`HTTPException` for anything but an image within the
    size cap.  A PNG is stripped of the ancillary chunks that trip
    Pillow-based inference servers, the same best-effort pass
    ``read_binary_document`` applies to an image read off disk.
    """
    if (
        not isinstance(item, BinaryContent)
        or item.media_type not in INGESTIBLE_IMAGE_MEDIA_TYPES
    ):
        raise HTTPException(
            status_code=400,
            detail=_ONLY_IMAGES.current,
        )

    limit = settings.limits.max_attachment_bytes
    if len(item.data) > limit:
        raise HTTPException(
            status_code=400,
            detail=_image_too_large(limit).current,
        )

    item.data = sanitize_image_bytes(item.data, item.media_type)


def _accept_attachments(messages: Sequence[ModelMessage]) -> None:
    """Admit the attachments of ``ChatAdapter.messages``, rejecting non-images.

    Takes the client's new prompt alone, never the replayed prefix, so an
    already-admitted image is never re-scanned on a later turn.  Anything
    but an image belongs in a workspace instead (see ``AGENTS.md``).

    Check the count before sanitizing any images in an over-cap request.
    """
    cap = settings.multimodal.max_images
    items = list(_attached_items(messages))

    if cap is not None and len(items) > cap:
        raise HTTPException(
            status_code=400,
            detail=_too_many_images(cap).current,
        )

    for item in items:
        _accept_attachment(item)


async def _run_chat(conversation: OwnedConversation, request: Request) -> Response:
    """Stream a chat turn for *conversation* via the Vercel AI protocol.

    A turn that carries a new user message returns its tree-node id in the
    ``X-Message-Id`` response header, under the same CORS constraint as
    ``X-Conversation-Id``.

    A request flagged ``compact`` compacts the active path instead of running
    a turn.
    """
    # The body is the AI SDK's, with the run configuration spliced in beside
    # its messages. Extras are ignored.
    config = ChatRequestConfig.model_validate(await request.json())

    run_prefix = build_run_prefix(config, conversation)
    model_settings = thinking_model_settings(config.reasoning_effort, run_prefix.llm)

    try:
        # `from_request` is typed to return the base adapter with erased type
        # parameters; the runtime object is a `ChatAdapter[UserDeps, str]`.
        adapter = cast(
            ChatAdapter[UserDeps, str],
            await ChatAdapter[UserDeps, str].from_request(
                request, agent=user_agent, sdk_version=SDK_VERSION
            ),
        )
        # Validates the client's answers to pending questions as well.
        resumed = adapter.deferred_tool_results is not None
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=_INVALID_CHAT_REQUEST.current
        ) from exc

    # Gate the new user message's attachments before the run. Sanitisation
    # lands in place, and the adapter caches `messages`, so `run_stream` and
    # persistence both see the admitted bytes.
    _accept_attachments(adapter.messages)

    # Live subagent transcript snapshots flow through this sink; the streaming
    # response drains it concurrently with the run (see `run_and_persist`).
    subagent_sink: asyncio.Queue[SubagentUpdate] = asyncio.Queue()
    deps = replace(run_prefix.deps, subagent_sink=subagent_sink)

    # History is server-authoritative: load the active-path prefix up to the
    # fork point from the DB and replay it, ignoring the browser echo.  The
    # client sends only the new message (none for a regenerate or compaction).
    prefix, fork_id = await resolve_fork(
        conversation,
        regenerate=config.trigger == "regenerate-message",
        message_id=config.message_id,
    )

    # Ahead of the approvals and answers below, which a compaction leaves pending.
    if config.compact:
        return _compact_active_path(conversation, adapter, run_prefix, prefix, fork_id)

    # Changes a program staged for an approval this request denies or abandons
    # can never be applied, so they go now rather than at the next boot sweep.
    if prefix and isinstance(prefix[-1], ModelResponse):
        approved = {
            call
            for call, decision in adapter.approval_decisions.items()
            if decision.approved
        }
        await discard_unapproved_changes(conversation, prefix[-1], approved)

    # A turn that ended awaiting approval or answers left its calls dangling so
    # this request could answer them.  One that carries anything else abandons
    # them, and the dangling calls have to be closed here or every later turn
    # replays them as a generic "interrupted" result and the model reissues the
    # same calls again and again.  Stored as its own node, since the run
    # replays it as part of the prefix.
    if not resumed:
        declined = abandon_pending_calls(prefix)
        if declined is not None:
            fork_id = await append_branch(conversation, fork_id, [declined])
            prefix = [*prefix, declined]

    record_known_urls(deps.known_urls, chain(prefix, adapter.messages))

    # Reserve the node id the new user message will be persisted under, so the
    # client can address it for an edit without waiting for a reload.  Only a
    # request carrying a user prompt reserves one: a regenerate and a
    # post-approval continuation start their delta with a message the client
    # never addresses.
    user_node_id = (
        new_id() if adapter.messages and is_user_request(adapter.messages[-1]) else None
    )

    stream = adapter.run_stream(
        deps=deps,
        output_type=[str, DeferredToolRequests],
        model=run_prefix.model,
        # Adds no instructions or tools, so the prefix stays the cached one.
        capabilities=[
            *run_prefix.capabilities,
            CompactOnOverflow(run_prefix),
        ],
        instructions=run_prefix.instructions,
        model_settings=model_settings,
        message_history=prefix,
        usage_limits=turn_usage_limits,
        # The server's id rather than the client's, so the run knows the one
        # conversation its `/tmp` and persisted turns belong to.
        conversation_id=conversation.id,
    )

    # Capture only the prefix length and whether it ends compacted, not the
    # prefix list, so the closure (held for the whole stream) does not pin the
    # full replayed history.
    prefix_len = len(prefix)
    ends_compacted = bool(prefix) and is_compaction(prefix[-1])

    async def persist(messages: Sequence[ModelMessage]) -> None:
        # Append only the turn's new messages (past the replayed prefix) as a
        # branch under the fork point.  The delta's user message is the one
        # whose id was announced, so it lands under exactly that id.
        delta = messages[prefix_len:]

        # A compaction that took the place of the regenerated prompt, the fork
        # node, goes ahead of it.
        ahead = messages[prefix_len - 1] if prefix_len else None
        if fork_id and ahead and is_compaction(ahead) and not ends_compacted:
            await insert_above(conversation, fork_id, ahead)
            delta = messages[prefix_len + 1 :]

        await append_branch(conversation, fork_id, delta, prompt_id=user_node_id)

    response = await run_and_persist(
        adapter, stream, persist=persist, subagent_sink=subagent_sink
    )
    if user_node_id is not None:
        response.headers["X-Message-Id"] = user_node_id
    return response


def _compact_active_path(
    conversation: OwnedConversation,
    adapter: ChatAdapter[UserDeps, str],
    run_prefix: RunPrefix,
    prefix: list[ModelMessage],
    fork_id: str | None,
) -> Response:
    """Stream the compaction of *prefix*, stored at its cut once made.

    Asked for under the same prefix a turn would run under, so the provider
    answers it from its cache.  The adapter frames the part like a run, so the
    client renders it as it renders the stored one on reload, and an error
    reaches it as a run error would.
    """
    cut = compaction_cut(prefix)

    if cut is None:
        raise HTTPException(status_code=400, detail=_NOTHING_TO_COMPACT.current)

    async def events() -> AsyncIterator[NativeEvent]:
        try:
            part = await compact(prefix[:cut], run_prefix)
            compaction = ModelResponse(parts=[part])
            # Shielded like a turn's write, so a client leaving during it
            # does not lose a summary already paid for.
            await asyncio.shield(
                insert_above(conversation, fork_id, compaction)
                if fork_id and cut < len(prefix)
                else append_branch(conversation, fork_id, [compaction])
            )

        except Exception:
            logger.exception("Failed to compact conversation %s", conversation.id)
            raise

        yield PartStartEvent(index=0, part=part)

    return adapter.streaming_response(adapter.transform_stream(events()))


@router.post("/conversations/chat")
async def create_new_conversation_chat(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Start a new conversation: mint a server ID and stream the first turn.

    The conversation row is created lazily when the turn is persisted (see
    :func:`append_branch`), so an abandoned chat never leaves an empty
    row behind.  Because the turn is persisted on an errored finish too,
    a first turn that errors still becomes a real row under the minted ID —
    which the client adopts so its retry targets that conversation instead
    of minting a duplicate.  The minted ID is returned in the
    ``X-Conversation-Id`` response header for the client to adopt.

    CORS constraint: the frontend can only read this header (and the
    ``X-Message-Id`` every turn returns) same-origin or when the edge proxy
    lists them under ``Access-Control-Expose-Headers``.  Same-origin needs no
    config; cross-origin deployments must expose them explicitly (custom
    response headers are hidden from JS otherwise).
    """
    conversation = OwnedConversation.mint(user)
    response = await _run_chat(conversation, request)
    response.headers["X-Conversation-Id"] = conversation.id
    return response


@router.post("/conversations/{conversation_id}/chat")
async def create_conversation_chat(conversation: Owned, request: Request) -> Response:
    """Continue an existing conversation.

    Only conversations that already hold a message exist as rows, so an ID
    the server never issued is a 404 rather than a silently created
    conversation — clients cannot mint their own IDs.
    """
    return await _run_chat(conversation, request)
