"""FastAPI server for the RAG agent."""

import logging
import shutil
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from nanoid import generate
from pydantic import BaseModel, Field
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from .agent import UserDeps, base_agent, explore_toolset, rag_toolset, user_agent
from .compaction import compact_conversation
from .auth import User, get_current_user
from .chunkers import (
    ChunkingPipeline,
    ChunkingPipelineInfo,
    get_chunking_pipelines_info,
)
from .chunks import (
    ChunkedDocument,
    chunk_document,
    delete_chunks,
    get_chunks,
    list_chunked_documents,
)
from .config import (
    BINARY_EXTENSIONS,
    TEXT_EXTENSIONS,
    FileExtension,
    sanitize_document_path,
    settings,
)
from .converters import (
    ConversionPipeline,
    ConversionPipelineInfo,
    get_converter,
    get_pipelines_info,
    resolve_auto_pipeline,
)
from .converters.base import LLMConvertOptions
from .mcp import mcp_app
from .observability import configure_observability
from .messages import (
    delete_conversation,
    list_conversations,
    load_conversation,
    load_messages,
    save_messages,
    update_conversation_title,
)
from .prompts import CITATION_INSTRUCTIONS, PERSONALITY_TEMPLATES
from .tokens import token_store
from .tools import DocumentFilter
from .types import (
    ChatRequestConfig,
    CompactConversationResponse,
    ConversationListResponse,
    ConversationSummary,
    CreateConversationResponse,
    CreateDirectoryRequest,
    CreateDirectoryResponse,
    CreateTokenRequest,
    CreateTokenResponse,
    DeleteConversationResponse,
    DeleteDirectoryResponse,
    DeleteDocumentResponse,
    DirectoryEntry,
    DirectoryTreeResponse,
    DocumentInfo,
    DocumentListResponse,
    GenerateTitleRequest,
    GenerateTitleResponse,
    LlmConfig,
    MoveDocumentRequest,
    MoveDocumentResponse,
    Personality,
    SettingsResponse,
    TokenInfo,
    UpdateTitleRequest,
    UploadDocumentResponse,
)

logger = logging.getLogger(__name__)


def resolve_llm_config(llm: LlmConfig, *, default_model: str = "") -> LlmConfig:
    """Apply server defaults to client-provided LLM config."""
    return LlmConfig(
        model=llm.model or default_model or settings.llm.model,
        api_key=llm.api_key or settings.llm.api_key,
        base_url=llm.base_url or settings.llm.base_url or None,
    )


class ReconvertRequest(BaseModel):
    """Request to reconvert a document from its original binary file."""

    conversion_pipeline: ConversionPipeline = ConversionPipeline.AUTO
    chunking_pipeline: ChunkingPipeline = ChunkingPipeline.AUTO
    llm: LlmConfig = Field(default_factory=LlmConfig)


mcp_http_app = mcp_app.http_app(path="/")

app = FastAPI(lifespan=mcp_http_app.lifespan)
configure_observability(app)

app.add_middleware(
    CORSMiddleware,  # type: ignore[arg-type]
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])


def _safe_path(filepath: str) -> str:
    """Sanitize a document filepath from a URL path parameter.

    Args:
        filepath: The raw filepath from the URL.

    Returns:
        Sanitized relative POSIX path.

    Raises:
        HTTPException: If the path is invalid.
    """
    try:
        return sanitize_document_path(filepath)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _cleanup_empty_parents(path: Path, stop_at: Path) -> None:
    """Remove empty parent directories up to *stop_at*."""
    parent = path.parent
    while parent != stop_at:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


@api_router.get("/settings")
async def get_settings(
    user: Annotated[User, Depends(get_current_user)],
) -> SettingsResponse:
    """Get server-side LLM settings (API key masked as boolean)."""
    return SettingsResponse(
        model=settings.llm.model,
        vision_model=settings.llm.vision_model,
        small_model=settings.llm.small_model,
        has_api_key=bool(settings.llm.api_key),
        base_url=settings.llm.base_url,
    )


@api_router.post("/conversation")
async def create_conversation(
    user: Annotated[User, Depends(get_current_user)],
) -> CreateConversationResponse:
    """Create a new conversation and return its ID."""
    conversation_id = generate()
    return CreateConversationResponse(id=conversation_id)


@api_router.get("/conversations")
async def get_conversations(
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationListResponse:
    """List all conversations with summary information."""
    conversations = list_conversations(user.id)
    return ConversationListResponse(
        conversations=conversations,
        total_count=len(conversations),
    )


@api_router.get("/conversation/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationSummary:
    """Get summary information for a specific conversation."""
    conversation = load_conversation(user.id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=len(conversation.messages),
        compacted_from=conversation.compacted_from,
    )


@api_router.get("/conversation/{conversation_id}/document-references")
async def get_conversation_document_references(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    """Get document references for a conversation."""
    conversation = load_conversation(user.id, conversation_id)
    if not conversation:
        return []
    return [ref.model_dump() for ref in conversation.document_references]


@api_router.put("/conversation/{conversation_id}/title")
async def update_title(
    conversation_id: str,
    request: UpdateTitleRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationSummary:
    """Update the title of a conversation."""
    if not update_conversation_title(user.id, conversation_id, request.title):
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation = load_conversation(user.id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=len(conversation.messages),
        compacted_from=conversation.compacted_from,
    )


def _extract_message_texts(
    messages: Sequence[ModelMessage], max_messages: int = 4
) -> list[str]:
    """Extract text content from conversation messages."""
    texts: list[str] = []
    for msg in messages:
        for part in msg.parts:
            if isinstance(part, (UserPromptPart, TextPart)):
                if isinstance(part.content, str) and (text := part.content.strip()):
                    texts.append(text[:500])
                    if len(texts) >= max_messages:
                        return texts
    return texts


@api_router.post("/conversation/{conversation_id}/generate-title")
async def generate_title(
    conversation_id: str,
    request: GenerateTitleRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> GenerateTitleResponse:
    """Generate a title for a conversation using an LLM."""
    conversation = load_conversation(user.id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = ModelMessagesTypeAdapter.validate_python(conversation.messages)
    messages_text = _extract_message_texts(messages)
    if not messages_text:
        return GenerateTitleResponse(title=conversation.title or "Untitled")

    conversation_preview = "\n---\n".join(messages_text)

    instructions = """Generate a short, descriptive title (max 60 characters) for the conversation.
The title should capture the main topic or question.
Return ONLY the title, no quotes or extra text.
"""

    resolved = resolve_llm_config(request.llm, default_model=settings.llm.small_model)

    try:
        result = await base_agent.run(
            f"Conversation:\n{conversation_preview}",
            model=OpenAIResponsesModel(
                resolved.model,
                provider=OpenAIProvider(
                    api_key=resolved.api_key,
                    base_url=resolved.base_url,
                ),
            ),
            instructions=instructions,
        )

        generated_title = result.output.strip().strip("\"'")
        if len(generated_title) > 100:
            generated_title = generated_title[:97] + "..."

        update_conversation_title(user.id, conversation_id, generated_title)

        return GenerateTitleResponse(title=generated_title)

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to generate title: {str(e)}"
        )


@api_router.delete("/conversation/{conversation_id}")
async def delete_conversation_endpoint(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DeleteConversationResponse:
    """Delete a conversation."""
    if not delete_conversation(user.id, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return DeleteConversationResponse(
        id=conversation_id,
        message="Conversation deleted successfully",
    )


@api_router.post("/conversation/{conversation_id}/compact")
async def compact_conversation_endpoint(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> CompactConversationResponse:
    """Compact a conversation by summarizing it into a new conversation.

    Creates a new conversation with a summary of the original, linking back
    to the original via ``compacted_from``. Uses the server's small model
    for summarization.
    """
    llm_config = resolve_llm_config(LlmConfig(), default_model=settings.llm.small_model)

    try:
        result = await compact_conversation(user.id, conversation_id, llm_config)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to compact conversation: {e!s}"
        )

    return CompactConversationResponse(
        new_conversation_id=result.new_conversation_id,
        summary=result.summary,
        message="Conversation compacted successfully",
    )


@api_router.get("/conversation/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> list[UIMessage]:
    """Get messages for a conversation in Vercel AI format."""
    messages = load_messages(user.id, conversation_id)
    if not messages:
        return []
    return VercelAIAdapter.dump_messages(messages)


async def _parse_chat_config(request: Request) -> ChatRequestConfig:
    """Parse chat configuration from the request body.

    Args:
        request: The incoming HTTP request.

    Returns:
        A ChatRequestConfig instance populated from body fields.
    """
    body = await request.json()
    llm = resolve_llm_config(LlmConfig(**(body.get("llm") or {})))
    try:
        personality = Personality(body.get("personality", "default"))
    except ValueError:
        personality = Personality.DEFAULT

    included_documents: list[str] = body.get("included_documents") or []
    excluded_documents: list[str] = body.get("excluded_documents") or []

    return ChatRequestConfig(
        conversation_id=body.get("conversation_id", ""),
        personality=personality,
        llm=llm,
        included_documents=included_documents,
        excluded_documents=excluded_documents,
    )


@api_router.post("/chat")
async def chat(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Handle chat requests using the Vercel AI Data Stream Protocol.

    Configuration is passed via the request body (see ChatRequestConfig).
    """
    config = await _parse_chat_config(request)

    if not config.conversation_id:
        raise HTTPException(
            status_code=400, detail="conversation_id is required in the request body"
        )

    document_filter: DocumentFilter | None = None
    if config.included_documents or config.excluded_documents:
        document_filter = DocumentFilter(
            included=frozenset(config.included_documents),
            excluded=frozenset(config.excluded_documents),
        )

    def on_complete(result: AgentRunResult[str]) -> None:
        """Save messages after the agent run completes."""
        save_messages(user.id, config.conversation_id, result.all_messages())

    return await VercelAIAdapter.dispatch_request(
        request,
        agent=user_agent,
        deps=UserDeps(user_id=user.id, document_filter=document_filter, llm=config.llm),
        model=OpenAIResponsesModel(
            config.llm.model,
            provider=OpenAIProvider(
                api_key=config.llm.api_key,
                base_url=config.llm.base_url,
            ),
        ),
        toolsets=[rag_toolset, explore_toolset],
        instructions=PERSONALITY_TEMPLATES[config.personality] + CITATION_INSTRUCTIONS,
        on_complete=on_complete,
    )


def _get_allowed_extensions() -> set[str]:
    """Get the set of allowed file extensions."""
    return {ext.value for ext in FileExtension}


def _get_text_extensions() -> set[str]:
    """Get the set of text-based file extensions (for indexing)."""
    return {ext.value for ext in TEXT_EXTENSIONS}


def _get_binary_extensions() -> set[str]:
    """Get the set of binary file extensions (require conversion)."""
    return {ext.value for ext in BINARY_EXTENSIONS}


def _is_text_file(suffix: str) -> bool:
    """Check if a file extension is a text-based format."""
    return suffix.lower() in _get_text_extensions()


# --- Document endpoints ---


@api_router.get("/documents")
async def list_documents(
    user: Annotated[User, Depends(get_current_user)],
) -> DocumentListResponse:
    """List all documents in the user's data directory.

    Returns only text-based files (including converted markdown files).
    Uses recursive search to include files in subdirectories.
    """
    data_dir = settings.get_user_documents_dir(user.id)
    originals_dir = settings.get_user_originals_dir(user.id)
    documents: list[DocumentInfo] = []
    chunk_counts = list_chunked_documents(user.id)

    # Build set of relative stems that have originals for reconversion
    original_stems: set[str] = set()
    if originals_dir.exists():
        for orig in originals_dir.rglob("*"):
            if orig.is_file():
                rel = orig.relative_to(originals_dir)
                original_stems.add(str((rel.parent / rel.stem).as_posix()))

    if data_dir.exists():
        text_extensions = _get_text_extensions()
        for file_path in sorted(data_dir.rglob("*")):
            if file_path.is_file() and file_path.suffix.lower() in text_extensions:
                rel_path = str(file_path.relative_to(data_dir).as_posix())
                rel = file_path.relative_to(data_dir)
                doc_stem = str((rel.parent / rel.stem).as_posix())
                stat = file_path.stat()
                documents.append(
                    DocumentInfo(
                        filename=rel_path,
                        size_bytes=stat.st_size,
                        modified_at=datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ),
                        chunk_count=chunk_counts.get(rel_path),
                        has_original=doc_stem in original_stems,
                    )
                )

    return DocumentListResponse(documents=documents, total_count=len(documents))


@api_router.put("/documents/{filepath:path}")
async def upload_document(
    filepath: str,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    conversion_pipeline: ConversionPipeline = Query(default=ConversionPipeline.AUTO),
    chunking_pipeline: ChunkingPipeline = Query(default=ChunkingPipeline.AUTO),
    llm_config: str = Form(default="{}"),
) -> UploadDocumentResponse:
    """Upload or replace a document.

    For binary files (PDF, DOCX, etc.), the file is converted to markdown
    using the specified conversion pipeline. The original is stored in originals/.
    All documents are chunked after saving using the specified chunking pipeline.

    Args:
        filepath: The target relative path (must have allowed extension).
        file: The uploaded file content.
        conversion_pipeline: The conversion pipeline to use for binary files.
        chunking_pipeline: The chunking pipeline to use.
        llm_config: JSON-encoded LLM configuration for conversion.
    """
    safe = _safe_path(filepath)
    llm = LlmConfig.model_validate_json(llm_config)
    resolved = resolve_llm_config(llm, default_model=settings.llm.vision_model)

    # Extract the basename for extension checking
    basename = safe.rsplit("/", 1)[-1] if "/" in safe else safe
    allowed_extensions = _get_allowed_extensions()
    suffix = "." + basename.rsplit(".", 1)[-1].lower() if "." in basename else ""

    if suffix not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file extension. Allowed: {', '.join(sorted(allowed_extensions))}",
        )

    content = await file.read()
    if len(content) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_bytes} bytes",
        )

    documents_dir = settings.get_user_documents_dir(user.id)

    # Handle text files directly
    if _is_text_file(suffix):
        file_path = documents_dir / safe
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)

        # Chunk the text file
        chunk_count = None
        chunking_used = None
        try:
            text_content = content.decode("utf-8")
            chunked = chunk_document(user.id, safe, text_content, chunking_pipeline)
            chunk_count = chunked.chunk_count
            chunking_used = chunked.chunking_pipeline
        except Exception as e:
            logger.warning("Chunking failed for %s: %s", safe, e)

        return UploadDocumentResponse(
            filename=safe,
            size_bytes=len(content),
            chunk_count=chunk_count,
            chunking_pipeline_used=chunking_used,
            message="Document uploaded successfully",
        )

    # Handle binary files - store original and convert to markdown
    originals_dir = settings.get_user_originals_dir(user.id)
    original_path = originals_dir / safe
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(content)

    # Resolve AUTO to a concrete pipeline
    resolved_conversion = conversion_pipeline
    if conversion_pipeline == ConversionPipeline.AUTO:
        resolved_conversion = resolve_auto_pipeline(basename)

    # Get the converter and check extension support
    try:
        converter = get_converter(resolved_conversion)
    except (ImportError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    if suffix not in converter.supported_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Conversion pipeline '{resolved_conversion.value}' does not support {suffix}. "
            f"Supported: {', '.join(sorted(converter.supported_extensions))}",
        )

    # Convert the document
    try:
        if resolved_conversion == ConversionPipeline.LLM:
            from .converters.llm_converter import LLMConverter

            assert isinstance(converter, LLMConverter)
            markdown_content = await converter.convert(
                original_path,
                options=LLMConvertOptions(
                    model=resolved.model,
                    api_key=resolved.api_key,
                    base_url=resolved.base_url,
                ),
            )
        else:
            markdown_content = await converter.convert(original_path)
    except ImportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Conversion failed: {e!s}",
        )

    # Save the converted markdown
    base_name = basename.rsplit(".", 1)[0]
    # Preserve directory structure for the converted file
    if "/" in safe:
        parent_dir = safe.rsplit("/", 1)[0]
        converted_relpath = f"{parent_dir}/{base_name}.md"
    else:
        converted_relpath = f"{base_name}.md"
    converted_path = documents_dir / converted_relpath
    converted_path.parent.mkdir(parents=True, exist_ok=True)
    converted_path.write_text(markdown_content, encoding="utf-8")

    # Chunk the converted document
    chunk_count = None
    chunking_used = None
    try:
        chunked = chunk_document(
            user.id, converted_relpath, markdown_content, chunking_pipeline
        )
        chunk_count = chunked.chunk_count
        chunking_used = chunked.chunking_pipeline
    except Exception as e:
        logger.warning("Chunking failed for %s: %s", converted_relpath, e)

    return UploadDocumentResponse(
        filename=safe,
        converted_filename=converted_relpath,
        size_bytes=len(content),
        conversion_pipeline_used=resolved_conversion.value,
        chunk_count=chunk_count,
        chunking_pipeline_used=chunking_used,
        message="Document uploaded and converted successfully",
    )


@api_router.get("/documents/{filepath:path}")
async def get_document_content(
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> PlainTextResponse:
    """Get the content of a document.

    Only text-based documents can be read directly.

    Args:
        filepath: The relative path to the document.
    """
    safe = _safe_path(filepath)
    data_dir = settings.get_user_documents_dir(user.id)
    file_path = data_dir / safe

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document not found")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    text_extensions = _get_text_extensions()
    if file_path.suffix.lower() not in text_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type")

    return PlainTextResponse(file_path.read_text(encoding="utf-8"))


@api_router.delete("/documents/{filepath:path}")
async def delete_document(
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DeleteDocumentResponse:
    """Delete a document and its associated chunks and original.

    Args:
        filepath: The relative path to the document.
    """
    safe = _safe_path(filepath)
    data_dir = settings.get_user_documents_dir(user.id)
    file_path = data_dir / safe

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document not found")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    text_extensions = _get_text_extensions()
    if file_path.suffix.lower() not in text_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type")

    file_path.unlink()
    _cleanup_empty_parents(file_path, data_dir)
    delete_chunks(user.id, safe)

    # Also delete the original if it exists
    originals_dir = settings.get_user_originals_dir(user.id)
    stem = Path(safe).stem
    parent = str(Path(safe).parent)
    if parent != ".":
        orig_dir = originals_dir / parent
    else:
        orig_dir = originals_dir
    if orig_dir.exists():
        for candidate in orig_dir.iterdir():
            if candidate.is_file() and candidate.stem == stem:
                candidate.unlink()
                _cleanup_empty_parents(candidate, originals_dir)
                break


    return DeleteDocumentResponse(
        filename=safe,
        message="Document deleted successfully",
    )


@api_router.get("/conversion-pipelines")
async def list_conversion_pipelines() -> list[ConversionPipelineInfo]:
    """Get metadata for all conversion pipelines."""
    return get_pipelines_info()


# Chunking endpoints


@api_router.get("/chunking-pipelines")
async def list_chunking_pipelines() -> list[ChunkingPipelineInfo]:
    """Get metadata for all chunking pipelines."""
    return get_chunking_pipelines_info()


@api_router.get("/documents/{filepath:path}/chunks")
async def get_document_chunks(
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> ChunkedDocument:
    """Get chunks for a document.

    Args:
        filepath: The relative document path.
    """
    safe = _safe_path(filepath)
    chunked = get_chunks(user.id, safe)
    if not chunked:
        raise HTTPException(status_code=404, detail="No chunks found for this document")
    return chunked


@api_router.post("/documents/{filepath:path}/rechunk")
async def rechunk_document(
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
    chunking_pipeline: ChunkingPipeline = Query(default=ChunkingPipeline.AUTO),
    chunk_size: int = Query(default=2048, ge=64, le=16384),
) -> ChunkedDocument:
    """Re-chunk a document with different settings.

    Args:
        filepath: The relative document path.
        chunking_pipeline: The chunking pipeline to use.
        chunk_size: The target chunk size in tokens.
    """
    safe = _safe_path(filepath)
    data_dir = settings.get_user_documents_dir(user.id)
    file_path = data_dir / safe

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")

    text_content = file_path.read_text(encoding="utf-8")

    try:
        return chunk_document(
            user.id, safe, text_content, chunking_pipeline, chunk_size
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Chunking failed: {e!s}",
        )


@api_router.post("/documents/{filepath:path}/reconvert")
async def reconvert_document(
    filepath: str,
    request: ReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> UploadDocumentResponse:
    """Re-convert a document from its original binary file.

    Finds the original file by matching the stem in originals/, converts it
    again with the specified pipeline, overwrites the text file, and rechunks.

    Args:
        filepath: The text document relative path (e.g. "projects/report.md").
        request: Reconversion options including pipeline and LLM config.
    """
    safe = _safe_path(filepath)
    resolved = resolve_llm_config(request.llm, default_model=settings.llm.vision_model)

    originals_dir = settings.get_user_originals_dir(user.id)
    documents_dir = settings.get_user_documents_dir(user.id)

    # Find original file by matching stem in the mirrored directory structure
    safe_path = Path(safe)
    target_stem = safe_path.stem
    parent = str(safe_path.parent)
    if parent != ".":
        orig_search_dir = originals_dir / parent
    else:
        orig_search_dir = originals_dir

    original_path = None
    if orig_search_dir.exists():
        for candidate in orig_search_dir.iterdir():
            if candidate.is_file() and candidate.stem == target_stem:
                original_path = candidate
                break

    if not original_path:
        raise HTTPException(
            status_code=404,
            detail=f"No original file found for '{safe}'",
        )

    # Resolve AUTO conversion pipeline
    resolved_conversion = request.conversion_pipeline
    if request.conversion_pipeline == ConversionPipeline.AUTO:
        resolved_conversion = resolve_auto_pipeline(original_path.name)

    # Get the converter
    try:
        converter = get_converter(resolved_conversion)
    except (ImportError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    suffix = original_path.suffix.lower()
    if suffix not in converter.supported_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Conversion pipeline '{resolved_conversion.value}' does not support {suffix}. "
            f"Supported: {', '.join(sorted(converter.supported_extensions))}",
        )

    # Convert the document
    try:
        if resolved_conversion == ConversionPipeline.LLM:
            from .converters.llm_converter import LLMConverter

            assert isinstance(converter, LLMConverter)
            markdown_content = await converter.convert(
                original_path,
                options=LLMConvertOptions(
                    model=resolved.model,
                    api_key=resolved.api_key,
                    base_url=resolved.base_url,
                ),
            )
        else:
            markdown_content = await converter.convert(original_path)
    except ImportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Conversion failed: {e!s}",
        )

    # Overwrite the text file
    converted_path = documents_dir / safe
    converted_path.parent.mkdir(parents=True, exist_ok=True)
    converted_path.write_text(markdown_content, encoding="utf-8")
    stat = converted_path.stat()


    # Rechunk the new content
    chunk_count = None
    chunking_used = None
    try:
        chunked = chunk_document(
            user.id, safe, markdown_content, request.chunking_pipeline
        )
        chunk_count = chunked.chunk_count
        chunking_used = chunked.chunking_pipeline
    except Exception as e:
        logger.warning("Chunking failed for %s: %s", safe, e)

    return UploadDocumentResponse(
        filename=original_path.name,
        converted_filename=safe,
        size_bytes=stat.st_size,
        conversion_pipeline_used=resolved_conversion.value,
        chunk_count=chunk_count,
        chunking_pipeline_used=chunking_used,
        message="Document reconverted successfully",
    )


# --- Document move endpoint ---


@api_router.post("/documents/{filepath:path}/move")
async def move_document(
    filepath: str,
    request: MoveDocumentRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> MoveDocumentResponse:
    """Move a document to a new location.

    Moves the document file, its chunks JSON, and its original (if any)
    to the new destination path.

    Args:
        filepath: The current relative path of the document.
        request: The move destination.
    """
    src = _safe_path(filepath)
    dst = _safe_path(request.destination)

    if src == dst:
        raise HTTPException(status_code=400, detail="Source and destination are the same")

    documents_dir = settings.get_user_documents_dir(user.id)
    chunks_dir = settings.get_user_chunks_dir(user.id)
    originals_dir = settings.get_user_originals_dir(user.id)

    src_doc = documents_dir / src
    if not src_doc.exists() or not src_doc.is_file():
        raise HTTPException(status_code=404, detail="Document not found")

    dst_doc = documents_dir / dst
    if dst_doc.exists():
        raise HTTPException(status_code=409, detail="Destination already exists")

    # Move the document file
    dst_doc.parent.mkdir(parents=True, exist_ok=True)
    src_doc.rename(dst_doc)
    _cleanup_empty_parents(src_doc, documents_dir)

    # Move chunks if they exist
    src_chunks = chunks_dir / f"{src}.json"
    if src_chunks.exists():
        dst_chunks = chunks_dir / f"{dst}.json"
        dst_chunks.parent.mkdir(parents=True, exist_ok=True)
        src_chunks.rename(dst_chunks)
        _cleanup_empty_parents(src_chunks, chunks_dir)

    # Move original if it exists (search by stem)
    src_path = Path(src)
    src_stem = src_path.stem
    src_parent = str(src_path.parent)
    if src_parent != ".":
        orig_search_dir = originals_dir / src_parent
    else:
        orig_search_dir = originals_dir

    if orig_search_dir.exists():
        for candidate in orig_search_dir.iterdir():
            if candidate.is_file() and candidate.stem == src_stem:
                dst_path = Path(dst)
                dst_parent = str(dst_path.parent)
                if dst_parent != ".":
                    dst_orig_dir = originals_dir / dst_parent
                else:
                    dst_orig_dir = originals_dir
                dst_orig_dir.mkdir(parents=True, exist_ok=True)
                dst_orig = dst_orig_dir / (dst_path.stem + candidate.suffix)
                candidate.rename(dst_orig)
                _cleanup_empty_parents(candidate, originals_dir)
                break


    return MoveDocumentResponse(
        source=src,
        destination=dst,
        message="Document moved successfully",
    )


# --- Directory endpoints ---


def _build_directory_tree(
    dir_path: Path,
    root_path: Path,
    chunk_counts: dict[str, int],
    original_stems: set[str],
    text_extensions: set[str],
) -> DirectoryEntry:
    """Recursively build a directory tree.

    Args:
        dir_path: The current directory to scan.
        root_path: The documents root directory for computing relative paths.
        chunk_counts: Mapping of relative document paths to chunk counts.
        original_stems: Set of relative stems that have originals.
        text_extensions: Set of text file extensions.

    Returns:
        A DirectoryEntry representing this directory and its children.
    """
    rel = str(dir_path.relative_to(root_path).as_posix())
    name = dir_path.name if dir_path != root_path else ""
    entry_path = rel if rel != "." else ""

    children: list[DirectoryEntry] = []

    if dir_path.exists():
        for item in sorted(dir_path.iterdir()):
            if item.is_dir():
                children.append(
                    _build_directory_tree(
                        item, root_path, chunk_counts, original_stems, text_extensions
                    )
                )
            elif item.is_file() and item.suffix.lower() in text_extensions:
                file_rel = str(item.relative_to(root_path).as_posix())
                item_rel = item.relative_to(root_path)
                doc_stem = str((item_rel.parent / item_rel.stem).as_posix())
                stat = item.stat()
                children.append(
                    DirectoryEntry(
                        type="file",
                        name=item.name,
                        path=file_rel,
                        size_bytes=stat.st_size,
                        modified_at=datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ),
                        chunk_count=chunk_counts.get(file_rel),
                        has_original=doc_stem in original_stems,
                    )
                )

    return DirectoryEntry(
        type="directory",
        name=name,
        path=entry_path,
        children=children,
    )


@api_router.get("/directories/tree")
async def get_directory_tree(
    user: Annotated[User, Depends(get_current_user)],
) -> DirectoryTreeResponse:
    """Build a recursive directory tree from the user's documents directory."""
    documents_dir = settings.get_user_documents_dir(user.id)
    originals_dir = settings.get_user_originals_dir(user.id)
    chunk_counts = list_chunked_documents(user.id)
    text_extensions = _get_text_extensions()

    # Build set of relative stems that have originals
    original_stems: set[str] = set()
    if originals_dir.exists():
        for orig in originals_dir.rglob("*"):
            if orig.is_file():
                rel = orig.relative_to(originals_dir)
                original_stems.add(str((rel.parent / rel.stem).as_posix()))

    root = _build_directory_tree(
        documents_dir, documents_dir, chunk_counts, original_stems, text_extensions
    )

    # Count totals
    total_files = 0
    total_dirs = 0

    def _count(entry: DirectoryEntry) -> None:
        nonlocal total_files, total_dirs
        if entry.type == "file":
            total_files += 1
        elif entry.type == "directory":
            total_dirs += 1
            for child in entry.children or []:
                _count(child)

    for child in root.children or []:
        _count(child)

    return DirectoryTreeResponse(
        root=root,
        total_files=total_files,
        total_directories=total_dirs,
    )


@api_router.post("/directories")
async def create_directory(
    request: CreateDirectoryRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> CreateDirectoryResponse:
    """Create a new directory within the documents directory.

    Args:
        request: The directory path to create.
    """
    safe = _safe_path(request.path)
    documents_dir = settings.get_user_documents_dir(user.id)
    dir_path = documents_dir / safe

    if dir_path.exists():
        raise HTTPException(status_code=409, detail="Directory already exists")

    dir_path.mkdir(parents=True, exist_ok=True)

    return CreateDirectoryResponse(
        path=safe,
        message="Directory created successfully",
    )


@api_router.delete("/directories/{dirpath:path}")
async def delete_directory(
    dirpath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DeleteDirectoryResponse:
    """Delete a directory and all its contents.

    Also deletes matching subdirectories in chunks/ and originals/.

    Args:
        dirpath: The relative directory path to delete.
    """
    safe = _safe_path(dirpath)
    documents_dir = settings.get_user_documents_dir(user.id)
    chunks_dir = settings.get_user_chunks_dir(user.id)
    originals_dir = settings.get_user_originals_dir(user.id)

    dir_path = documents_dir / safe
    if not dir_path.exists() or not dir_path.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    # Count files before deletion
    text_extensions = _get_text_extensions()
    files_deleted = sum(
        1 for f in dir_path.rglob("*") if f.is_file() and f.suffix.lower() in text_extensions
    )

    # Delete the directory and all contents
    shutil.rmtree(dir_path)
    _cleanup_empty_parents(dir_path, documents_dir)

    # Delete matching chunks subdirectory
    chunks_subdir = chunks_dir / safe
    if chunks_subdir.exists() and chunks_subdir.is_dir():
        shutil.rmtree(chunks_subdir)
        _cleanup_empty_parents(chunks_subdir, chunks_dir)

    # Delete matching originals subdirectory
    originals_subdir = originals_dir / safe
    if originals_subdir.exists() and originals_subdir.is_dir():
        shutil.rmtree(originals_subdir)
        _cleanup_empty_parents(originals_subdir, originals_dir)


    return DeleteDirectoryResponse(
        path=safe,
        files_deleted=files_deleted,
        message="Directory deleted successfully",
    )


# Token management endpoints


@api_router.post("/tokens")
async def create_token(
    request: CreateTokenRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> CreateTokenResponse:
    """Create a new personal access token.

    The raw token is only returned once and cannot be retrieved later.
    """
    created = token_store.create_token(
        user_id=user.id,
        name=request.name,
        expires_in_days=request.expires_in_days,
    )

    return CreateTokenResponse(
        token=created.raw_token,
        id=created.info.id,
        name=created.info.name,
        created_at=created.info.created_at,
        expires_at=created.info.expires_at,
    )


@api_router.get("/tokens")
async def list_tokens(
    user: Annotated[User, Depends(get_current_user)],
) -> list[TokenInfo]:
    """List all personal access tokens for the current user."""
    return token_store.list_tokens(user.id)


@api_router.delete("/tokens/{token_id}")
async def revoke_token(
    token_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Revoke a personal access token."""
    if not token_store.revoke_token(user.id, token_id):
        raise HTTPException(status_code=404, detail="Token not found")


app.include_router(api_router)
app.mount("/mcp", mcp_http_app)
