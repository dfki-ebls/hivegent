"""Routes for conversations and chat orchestration."""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import replace
from itertools import chain
from typing import Annotated, cast
from uuid import UUID

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
from starlette.responses import Response, StreamingResponse

from ... import conversation_files, workspace
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
)
from ...agents.subagent_events import SubagentUpdate
from ...auth import get_current_user
from ...config import RagoldSettings, parse_uuid, settings
from ...converters import INGESTIBLE_IMAGE_MEDIA_TYPES
from ...converters.images import sanitize_image_bytes
from ...db.conversations import (
    UNTITLED,
    ConversationSummary,
    MessageIdTaken,
    MessagePair,
    OwnedConversation,
    append_branch,
    claim_conversation,
    import_conversation,
    insert_above,
    is_user_request,
    list_conversations,
    load_active_for_display,
    load_conversation,
    load_conversation_summary,
    open_conversation,
    owned_conversation,
    prompt_items,
    prompt_text,
    resolve_fork,
    set_conversation_title,
)
from ...humanize import format_bytes, pluralize
from ...l10n import Localized, current_language
from ...llm import (
    model_from_config,
    thinking_model_settings,
)
from ...ragold import RagoldRequest, build_archive, iter_archive
from ...store import User
from ...tmp import open_tmp
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
from ..common import (
    CONVERSATION_NOT_FOUND,
    build_run_prefix,
    found,
    prepare_llm_config,
    resolve_workspace_path,
)
from ..operations import attachment_disposition
from ..turns import Replay, abandon_pending_calls, pending_requests
from ..vercel import (
    SDK_VERSION,
    ChatAdapter,
    agent_events,
    dump_messages_with_ids,
    load_archived,
    run_and_persist,
)

__all__ = ["router"]

logger = logging.getLogger(__name__)
router = APIRouter()

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
_INVALID_MESSAGE_ID = Localized(
    en="A new message needs a UUID as its id",
    de="Eine neue Nachricht braucht eine UUID als ID",
)
_MESSAGE_ID_TAKEN = Localized(
    en="A message with this id exists already, send it under a new id",
    de="Eine Nachricht mit dieser ID existiert bereits, sende sie unter einer neuen ID",
)
_RAGOLD_DISABLED = Localized(
    en="RAGold export is disabled", de="Der RAGold-Export ist deaktiviert"
)


async def _owned_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> OwnedConversation:
    """The path's conversation, once the database names the caller its owner.

    Every ``{conversation_id}`` route takes the :class:`OwnedConversation` its
    message tree and ``/tmp`` need from here, and an id that is no UUID names
    no conversation.
    """
    parsed = parse_uuid(conversation_id)

    return found(None if parsed is None else await owned_conversation(user, parsed))


type Owned = Annotated[OwnedConversation, Depends(_owned_conversation)]


async def _opened_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> tuple[OwnedConversation, bool]:
    """The path's conversation and whether its row exists (:func:`open_conversation`)."""
    parsed = parse_uuid(conversation_id)

    return found(None if parsed is None else await open_conversation(user, parsed))


type Opened = Annotated[tuple[OwnedConversation, bool], Depends(_opened_conversation)]


def _require_ragold() -> RagoldSettings:
    """Hide the RAGold transfer while it is not configured."""
    if settings.ragold.url is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _RAGOLD_DISABLED.current)

    return settings.ragold


type RagoldConfigured = Annotated[RagoldSettings, Depends(_require_ragold)]


@router.get("/conversations")
async def get_conversations(
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationListResponse:
    """List all conversations with summary information."""
    return ConversationListResponse(conversations=await list_conversations(user))


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation: Owned) -> ConversationSummary:
    """Get summary information for a specific conversation."""
    return found(await load_conversation_summary(conversation))


@router.patch("/conversations/{conversation_id}")
async def update_conversation_title(
    conversation: Owned, request: UpdateTitleRequest
) -> ConversationSummary:
    """Update the title of a conversation."""
    return found(await set_conversation_title(conversation, request.title))


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
    data = found(await load_conversation(conversation))

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
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND.current)


@router.delete("/conversations", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_conversations_route(
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Delete all conversations for the authenticated user, and their files on disk."""
    await conversation_files.delete_conversations(user)


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(opened: Opened) -> list[UIMessage]:
    """Get the active path of a conversation in Vercel AI format.

    Each ``UIMessage.id`` is the tree-node id the client addresses for edit /
    regenerate, and forking nodes carry branch metadata.  An absent id has no
    messages yet, so a client opens a new chat at the id it chose.
    """
    conversation, exists = opened
    result = await load_active_for_display(conversation) if exists else None

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
    summary = found(await load_conversation_summary(conversation))

    result = await load_active_for_display(conversation)
    pairs, siblings = result if result else ([], {})
    server = ServerConversation(
        id=str(summary.id),
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


@router.post("/conversations/{conversation_id}/ragold")
async def export_ragold_route(
    conversation: Owned, request: RagoldRequest, ragold: RagoldConfigured
) -> StreamingResponse:
    """Build one RAGold gold entry with its original documents as a zip.

    Every document resolves with the caller's access and the zip is complete
    before the first byte is sent, so a failure is an HTTP error, never a
    truncated archive.
    """
    user = conversation.user
    paths = list(dict.fromkeys(request.documents))
    # Every scope is checked before any document is read.
    located = [resolve_workspace_path(user, path) for path in paths]
    sources = await asyncio.gather(
        *(workspace.find_source(store, safe) for store, safe in located)
    )
    files = dict(zip(paths, sources, strict=True))

    spool = await asyncio.to_thread(
        build_archive,
        request,
        files,
        author=user.name or user.id,
        project=ragold.project,
        language=current_language(),
    )

    return StreamingResponse(
        iter_archive(spool),
        media_type="application/zip",
        headers={"Content-Disposition": attachment_disposition("ragold.zip")},
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
        return await import_conversation(user, load_archived(ui_messages), title=title)
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


async def _run_chat(
    conversation: OwnedConversation, request: Request, *, exists: bool
) -> Response:
    """Stream a chat turn for *conversation* via the Vercel AI protocol.

    A turn that carries a new user message stores it under the id the client
    gave it (:func:`_prompt_id`).  A request flagged ``compact`` compacts the
    active path instead of running a turn.  A conversation whose row does not
    *exist* yet is claimed right before the run.
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
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=_INVALID_CHAT_REQUEST.current
        ) from exc

    # Gate the new user message's attachments before the run. Sanitisation
    # lands in place, and the adapter caches `messages`, so `run_stream` and
    # persistence both see the admitted bytes.
    _accept_attachments(adapter.messages)

    user_node_id = _prompt_id(adapter)

    # Live subagent transcript snapshots flow through this sink; the streaming
    # response drains it concurrently with the run (see `run_and_persist`).
    subagent_sink: asyncio.Queue[SubagentUpdate] = asyncio.Queue()
    deps = replace(run_prefix.deps, subagent_sink=subagent_sink)

    # History is server-authoritative: load the active-path prefix up to the
    # fork point from the DB and replay it, ignoring the browser echo.  The
    # client sends only the new message (none for a regenerate or compaction).
    # Its id is checked before anything is written or run, since the turn is
    # persisted only once it streamed and a taken id would lose it then.
    try:
        prefix, fork_id = await resolve_fork(
            conversation,
            regenerate=config.trigger == "regenerate-message",
            message_id=config.message_id,
            prompt_id=user_node_id,
        )
    except MessageIdTaken as exc:
        raise HTTPException(status_code=409, detail=_MESSAGE_ID_TAKEN.current) from exc

    # Ahead of the approvals and answers below, which a compaction leaves pending.
    if config.compact:
        return _compact_active_path(conversation, adapter, run_prefix, prefix, fork_id)

    # The client's decisions and answers count only for the calls the stored
    # history still awaits, each with the kind of result it was deferred for.
    pending = pending_requests(prefix)
    try:
        deferred = adapter.resume(pending)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=_INVALID_CHAT_REQUEST.current
        ) from exc

    # Every check that can refuse the request is behind, so a refused one
    # leaves no row.  An id taken since it was opened is no longer the caller's.
    if not exists and not await claim_conversation(conversation):
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND.current)

    open_tmp(conversation)

    # Changes a program staged for an approval this request denies or abandons
    # can never be applied, so they go now rather than at the next boot sweep.
    if pending is not None:
        approved = {
            call
            for call, decision in adapter.approval_decisions.items()
            if decision.approved
        }
        await discard_unapproved_changes(conversation, pending.approvals, approved)

    # A turn that ended awaiting approval or answers left its calls dangling so
    # this request could answer them.  One that carries anything else abandons
    # them, and the dangling calls have to be closed here or every later turn
    # replays them as a generic "interrupted" result and the model reissues the
    # same calls again and again.  Stored as its own node, since the run
    # replays it as part of the prefix.
    if deferred is None:
        declined = abandon_pending_calls(pending)
        if declined is not None:
            fork_id = await append_branch(conversation, fork_id, [declined])
            prefix = [*prefix, declined]

    record_known_urls(deps.known_urls, chain(prefix, adapter.messages))

    stream = adapter.transform_stream(
        agent_events(
            user_agent.run_stream_events(
                adapter.prompt,
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
                deferred_tool_results=deferred,
                usage_limits=turn_usage_limits,
                # The server's id rather than the client's, so the run knows the
                # one conversation its `/tmp` and persisted turns belong to.
                conversation_id=str(conversation.id),
            )
        )
    )

    replay = Replay.of(prefix)

    async def persist(messages: Sequence[ModelMessage]) -> None:
        # Append only the turn's new messages (past the replayed prefix) as a
        # branch under the fork point.  The delta's user message is the
        # client's, so it lands under the id the client gave it.  A compaction
        # that took the place of the regenerated prompt, the fork node, goes
        # ahead of it.
        above, delta = replay.turn(messages)

        if above is not None and fork_id:
            await insert_above(conversation, fork_id, above)

        await append_branch(conversation, fork_id, delta, prompt_id=user_node_id)

    return await run_and_persist(
        adapter, stream, persist=persist, subagent_sink=subagent_sink
    )


def _prompt_id(adapter: ChatAdapter[UserDeps, str]) -> UUID | None:
    """The id the client gave the turn's new user message, ``None`` without one.

    A regenerate, a compaction, and a post-approval continuation carry no new
    user message, so the client addresses none of their nodes.

    Raises:
        HTTPException: 422 when the id is no UUID.
    """
    if not adapter.messages or not is_user_request(adapter.messages[-1]):
        return None

    message_id = next(
        message.id
        for message in reversed(adapter.run_input.messages)
        if message.role == "user"
    )

    parsed = parse_uuid(message_id)

    if parsed is None:
        raise HTTPException(status_code=422, detail=_INVALID_MESSAGE_ID.current)

    return parsed


def _compact_active_path(
    conversation: OwnedConversation,
    adapter: ChatAdapter[UserDeps, str],
    run_prefix: RunPrefix,
    prefix: list[ModelMessage],
    fork_id: UUID | None,
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

    open_tmp(conversation)

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


@router.post("/conversations/{conversation_id}/chat")
async def create_conversation_chat(opened: Opened, request: Request) -> Response:
    """Run a turn in a conversation, starting it at a fresh id the client chose."""
    conversation, exists = opened

    return await _run_chat(conversation, request, exists=exists)
