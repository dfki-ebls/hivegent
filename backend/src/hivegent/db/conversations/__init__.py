"""Conversations, kept by a pydantic-ai ``ConversationStore`` on PostgreSQL.

The submodules hold the store (:mod:`~hivegent.db.conversations.postgres`), the queries
(:mod:`~hivegent.db.conversations.queries`), the owned conversation and its shapes
(:mod:`~hivegent.db.conversations.models`), and what is read off the messages
(:mod:`~hivegent.db.conversations.messages`).
"""

from .messages import (
    CHAT_ERROR_KEY,
    HIDDEN_KEY,
    UNTITLED,
    extract_title,
    is_hidden,
    is_prompt,
    is_user_request,
    prompt_items,
    prompt_text,
    render_prompt,
    title_line,
)
from .models import ConversationData, ConversationSummary, OwnedConversation
from .postgres import OwnedConversationStore, PostgresConversationStore, store
from .queries import (
    claim_conversation,
    conversation_owners,
    import_conversation,
    list_conversations,
    load_conversation,
    load_conversation_data,
    load_conversation_summary,
    open_conversation,
    owned_conversation,
    owned_conversations,
    remove_conversation,
    remove_owned_conversations,
    search_conversation,
    set_conversation_title,
)

__all__ = [
    "CHAT_ERROR_KEY",
    "HIDDEN_KEY",
    "UNTITLED",
    "ConversationData",
    "ConversationSummary",
    "OwnedConversation",
    "OwnedConversationStore",
    "PostgresConversationStore",
    "claim_conversation",
    "conversation_owners",
    "extract_title",
    "import_conversation",
    "is_hidden",
    "is_prompt",
    "is_user_request",
    "list_conversations",
    "load_conversation",
    "load_conversation_data",
    "load_conversation_summary",
    "open_conversation",
    "owned_conversation",
    "owned_conversations",
    "prompt_items",
    "prompt_text",
    "remove_conversation",
    "remove_owned_conversations",
    "render_prompt",
    "search_conversation",
    "set_conversation_title",
    "store",
    "title_line",
]
