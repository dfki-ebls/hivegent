"""FastAPI server for the RAG agent."""

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
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

from .agent import UserDeps, base_agent, rag_toolset, user_agent
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
from .config import BINARY_EXTENSIONS, TEXT_EXTENSIONS, FileExtension, settings
from .converters import (
    ConversionPipeline,
    ConversionPipelineInfo,
    get_converter,
    get_pipelines_info,
    resolve_auto_pipeline,
)
from .converters.base import LLMConvertOptions
from .documents import reload_user_documents
from .mcp import mcp_app
from .messages import (
    delete_conversation,
    list_conversations,
    load_conversation,
    load_messages,
    save_messages,
    update_conversation_title,
)
from .prompts import PERSONALITY_TEMPLATES
from .tokens import token_store
from .types import (
    ChatRequestConfig,
    ConversationListResponse,
    ConversationSummary,
    CreateConversationResponse,
    CreateTokenRequest,
    CreateTokenResponse,
    DeleteConversationResponse,
    DeleteDocumentResponse,
    DocumentInfo,
    DocumentListResponse,
    GenerateTitleRequest,
    GenerateTitleResponse,
    LlmConfig,
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

app.add_middleware(
    CORSMiddleware,  # type: ignore[arg-type]
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])


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
                    api_key=resolved.api_key or "not-needed",
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

    return ChatRequestConfig(
        conversation_id=body.get("conversation_id", ""),
        personality=personality,
        llm=llm,
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

    def on_complete(result: AgentRunResult[str]) -> None:
        """Save messages after the agent run completes."""
        save_messages(user.id, config.conversation_id, result.all_messages())

    return await VercelAIAdapter.dispatch_request(
        request,
        agent=user_agent,
        deps=UserDeps(user_id=user.id),
        model=OpenAIResponsesModel(
            config.llm.model,
            provider=OpenAIProvider(
                api_key=config.llm.api_key or "not-needed",
                base_url=config.llm.base_url,
            ),
        ),
        toolsets=[rag_toolset],
        instructions=PERSONALITY_TEMPLATES[config.personality],
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


@api_router.get("/documents")
async def list_documents(
    user: Annotated[User, Depends(get_current_user)],
) -> DocumentListResponse:
    """List all documents in the user's data directory.

    Returns only text-based files (including converted markdown files).
    """
    data_dir = settings.get_user_documents_dir(user.id)
    originals_dir = settings.get_user_originals_dir(user.id)
    documents: list[DocumentInfo] = []
    chunk_counts = list_chunked_documents(user.id)

    # Build set of stems that have originals for reconversion
    original_stems: set[str] = set()
    if originals_dir.exists():
        for orig in originals_dir.iterdir():
            if orig.is_file():
                original_stems.add(orig.stem)

    if data_dir.exists():
        text_extensions = _get_text_extensions()
        for file_path in sorted(data_dir.iterdir()):
            if file_path.is_file() and file_path.suffix.lower() in text_extensions:
                stat = file_path.stat()
                documents.append(
                    DocumentInfo(
                        filename=file_path.name,
                        size_bytes=stat.st_size,
                        modified_at=datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ),
                        chunk_count=chunk_counts.get(file_path.name),
                        has_original=file_path.stem in original_stems,
                    )
                )

    return DocumentListResponse(documents=documents, total_count=len(documents))


@api_router.put("/documents/{filename}")
async def upload_document(
    filename: str,
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
        filename: The target filename (must have allowed extension).
        file: The uploaded file content.
        conversion_pipeline: The conversion pipeline to use for binary files.
        chunking_pipeline: The chunking pipeline to use.
        llm_config: JSON-encoded LLM configuration for conversion.
    """
    llm = LlmConfig.model_validate_json(llm_config)
    resolved = resolve_llm_config(llm, default_model=settings.llm.vision_model)

    allowed_extensions = _get_allowed_extensions()
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

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
        file_path = documents_dir / filename
        file_path.write_bytes(content)
        reload_user_documents(user.id)

        # Chunk the text file
        chunk_count = None
        chunking_used = None
        try:
            text_content = content.decode("utf-8")
            chunked = chunk_document(user.id, filename, text_content, chunking_pipeline)
            chunk_count = chunked.chunk_count
            chunking_used = chunked.chunking_pipeline
        except Exception as e:
            logger.warning("Chunking failed for %s: %s", filename, e)

        return UploadDocumentResponse(
            filename=filename,
            size_bytes=len(content),
            chunk_count=chunk_count,
            chunking_pipeline_used=chunking_used,
            message="Document uploaded successfully",
        )

    # Handle binary files - store original and convert to markdown
    originals_dir = settings.get_user_originals_dir(user.id)
    original_path = originals_dir / filename
    original_path.write_bytes(content)

    # Resolve AUTO to a concrete pipeline
    resolved_conversion = conversion_pipeline
    if conversion_pipeline == ConversionPipeline.AUTO:
        resolved_conversion = resolve_auto_pipeline(filename)

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
    base_name = filename.rsplit(".", 1)[0]
    converted_filename = f"{base_name}.md"
    converted_path = documents_dir / converted_filename
    converted_path.write_text(markdown_content, encoding="utf-8")

    reload_user_documents(user.id)

    # Chunk the converted document
    chunk_count = None
    chunking_used = None
    try:
        chunked = chunk_document(
            user.id, converted_filename, markdown_content, chunking_pipeline
        )
        chunk_count = chunked.chunk_count
        chunking_used = chunked.chunking_pipeline
    except Exception as e:
        logger.warning("Chunking failed for %s: %s", converted_filename, e)

    return UploadDocumentResponse(
        filename=filename,
        converted_filename=converted_filename,
        size_bytes=len(content),
        conversion_pipeline_used=resolved_conversion.value,
        chunk_count=chunk_count,
        chunking_pipeline_used=chunking_used,
        message="Document uploaded and converted successfully",
    )


@api_router.get("/documents/{filename}")
async def get_document_content(
    filename: str,
    user: Annotated[User, Depends(get_current_user)],
) -> PlainTextResponse:
    """Get the content of a document.

    Only text-based documents can be read directly.

    Args:
        filename: The filename to read.
    """
    data_dir = settings.get_user_documents_dir(user.id)
    file_path = data_dir / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document not found")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    text_extensions = _get_text_extensions()
    if file_path.suffix.lower() not in text_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type")

    return PlainTextResponse(file_path.read_text(encoding="utf-8"))


@api_router.delete("/documents/{filename}")
async def delete_document(
    filename: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DeleteDocumentResponse:
    """Delete a document and its associated chunks.

    Args:
        filename: The filename to delete.
    """
    data_dir = settings.get_user_documents_dir(user.id)
    file_path = data_dir / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document not found")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    text_extensions = _get_text_extensions()
    if file_path.suffix.lower() not in text_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type")

    file_path.unlink()
    delete_chunks(user.id, filename)

    reload_user_documents(user.id)

    return DeleteDocumentResponse(
        filename=filename,
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


@api_router.get("/documents/{filename}/chunks")
async def get_document_chunks(
    filename: str,
    user: Annotated[User, Depends(get_current_user)],
) -> ChunkedDocument:
    """Get chunks for a document.

    Args:
        filename: The document filename.
    """
    chunked = get_chunks(user.id, filename)
    if not chunked:
        raise HTTPException(status_code=404, detail="No chunks found for this document")
    return chunked


@api_router.post("/documents/{filename}/rechunk")
async def rechunk_document(
    filename: str,
    user: Annotated[User, Depends(get_current_user)],
    chunking_pipeline: ChunkingPipeline = Query(default=ChunkingPipeline.AUTO),
    chunk_size: int = Query(default=2048, ge=64, le=16384),
) -> ChunkedDocument:
    """Re-chunk a document with different settings.

    Args:
        filename: The document filename.
        chunking_pipeline: The chunking pipeline to use.
        chunk_size: The target chunk size in tokens.
    """
    data_dir = settings.get_user_documents_dir(user.id)
    file_path = data_dir / filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")

    text_content = file_path.read_text(encoding="utf-8")

    try:
        return chunk_document(
            user.id, filename, text_content, chunking_pipeline, chunk_size
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Chunking failed: {e!s}",
        )


@api_router.post("/documents/{filename}/reconvert")
async def reconvert_document(
    filename: str,
    request: ReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> UploadDocumentResponse:
    """Re-convert a document from its original binary file.

    Finds the original file by matching the stem in originals/, converts it
    again with the specified pipeline, overwrites the text file, and rechunks.

    Args:
        filename: The text document filename (e.g. "report.md").
        request: Reconversion options including pipeline and LLM config.
    """
    resolved = resolve_llm_config(request.llm, default_model=settings.llm.vision_model)

    originals_dir = settings.get_user_originals_dir(user.id)
    documents_dir = settings.get_user_documents_dir(user.id)

    # Find original file by matching stem
    target_stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    original_path = None
    for candidate in originals_dir.iterdir():
        if candidate.is_file() and candidate.stem == target_stem:
            original_path = candidate
            break

    if not original_path:
        raise HTTPException(
            status_code=404,
            detail=f"No original file found for '{filename}'",
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
    converted_path = documents_dir / filename
    converted_path.write_text(markdown_content, encoding="utf-8")
    stat = converted_path.stat()

    reload_user_documents(user.id)

    # Rechunk the new content
    chunk_count = None
    chunking_used = None
    try:
        chunked = chunk_document(
            user.id, filename, markdown_content, request.chunking_pipeline
        )
        chunk_count = chunked.chunk_count
        chunking_used = chunked.chunking_pipeline
    except Exception as e:
        logger.warning("Chunking failed for %s: %s", filename, e)

    return UploadDocumentResponse(
        filename=original_path.name,
        converted_filename=filename,
        size_bytes=stat.st_size,
        conversion_pipeline_used=resolved_conversion.value,
        chunk_count=chunk_count,
        chunking_pipeline_used=chunking_used,
        message="Document reconverted successfully",
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
