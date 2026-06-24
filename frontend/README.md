# Hivegent Frontend

## DB-first chat history

The backend owns conversation history as a message tree (see `backend/README.md`, "Conversations are a server-authoritative message tree").
The client no longer echoes the whole conversation: `prepareSendMessagesRequest` in `src/hooks/chat/use-hivegent-chat.ts` sends only the new message (none for a regenerate) plus `trigger` and `messageId`, and the server loads the active-path prefix from its store and forks or appends under the node addressed by `messageId`.
History is hydrated on load from `GET /conversations/{id}/messages` (in `use-conversation-history.ts`) exactly as before, except each `UIMessage.id` is now the server's tree-node id.

Submit, edit, regenerate, retry, and stop already work end-to-end against this backend.
Editing a message or regenerating a reply forks a sibling branch server-side and preserves the prior one, but the UI to navigate those branches is not built yet.

## TODO: branch-navigation UI (Phase 2)

The active branch is always the newest leaf — the backend stores no selection pointer (see `backend/README.md`).
Phase 2 keeps that model: **branch navigation is ephemeral client view state, never persisted server-side.**
Viewing an older branch just swaps the messages the client renders; the choice becomes durable only when the user _appends_ to that branch (continue, edit, or regenerate), because the appended chain is then the newest leaf the server already treats as active on the next load.
So there is **no** `/branches/select` endpoint, no `set_active_leaf`, and no schema change — only a read-only projection plus telling the server where a turn continues from.

The backend already emits the navigation data: `GET /conversations/{id}/messages` returns `UIMessage`s whose forking nodes carry `metadata.branch = { branchCount, branchIndex, siblingIds }`.
The AI Elements branch components already exist, unwired, in `src/components/ai-elements/message.tsx` (`MessageBranch`, `MessageBranchPrevious`, `MessageBranchNext`, `MessageBranchPage`).

Backend additions (both read-only, no writes, no new column):

- Generalize the active-path projection to anchor at a chosen leaf instead of the newest one: give `GET /conversations/{id}/messages` an optional `?branch={messageId}` that descends the addressed node to its branch tip and walks up to the root (the same `_load_active_path` / `dump_messages_with_ids` path, just a different anchor). Without the param it returns the newest branch, exactly as today.
- Let a turn continue a non-newest branch: the chat request must carry the continuation anchor (the id of the last message in the client's current view) so `resolve_fork` forks at that node rather than the global newest. Edit / regenerate keep their existing `messageId` semantics; when the client is already on the newest branch the anchor _is_ the newest leaf, so behaviour is unchanged.

Frontend steps:

- Add `loadBranch(conversationId, messageId)` to `src/lib/api.ts` that GETs `/conversations/{id}/messages?branch={messageId}` and returns `UIMessage[]`.
- Define a typed branch-metadata shape (e.g. in `src/lib/chat/`) and a guard that reads `message.metadata.branch` (it is `unknown` on `UIMessage`).
- In `MessageBubble.tsx` (and `parts/UserTextPart.tsx` / `MessagePart.tsx` for placement), render a prev/next selector showing `branchIndex + 1` of `branchCount` whenever `branchCount > 1`, on both user bubbles (edit forks) and assistant bubbles (regenerate forks), driven by which message carries `metadata.branch`.
- Wire the prev/next handlers in `ChatSidebar.tsx`: resolve the target sibling from `branch.siblingIds` plus the direction, call `loadBranch`, then `setMessages` with the returned path, and clear the fetched-documents panel. This is pure client view state — no server round-trip mutates anything. Disable the controls while streaming.
- When the user continues / edits / regenerates while viewing a branch, include the continuation anchor (the last rendered message id) in the chat request so the new chain forks off that branch and becomes the newest, hence active.
- Prefer a small presentational selector bound to `branchIndex` / `branchCount` over the existing `MessageBranch` context provider, which assumes it owns the alternative children rather than driving a server fetch.

The live partial-response gap is unrelated and tracked in `backend/README.md`: a turn stopped mid-stream is not persisted on pydantic-ai 1.x, so it will not appear after a reload until the v2 upgrade.
