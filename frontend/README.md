# Hivegent Frontend

## DB-first chat history

The backend owns conversation history as a message tree (see `backend/README.md`, "Conversations are a server-authoritative message tree").
The client no longer echoes the whole conversation: `prepareSendMessagesRequest` in `src/hooks/chat/use-hivegent-chat.ts` sends only the new message (none for a regenerate) plus `trigger` and `messageId`, and the server loads the active-path prefix from its store and forks or appends under the node addressed by `messageId`.
History is hydrated on load from `GET /conversations/{id}/messages` (in `use-conversation-history.ts`) exactly as before, except each `UIMessage.id` is now the server's tree-node id.

Submit, edit, regenerate, retry, and stop already work end-to-end against this backend.
Editing a message or regenerating a reply forks a sibling branch server-side and preserves the prior one, but the UI to navigate those branches is not built yet.

## TODO: branch-navigation UI (Phase 2)

The backend already exposes everything this needs.
`GET /conversations/{id}/messages` returns `UIMessage`s whose forking nodes carry `metadata.branch = { branchCount, branchIndex, siblingIds }`, and `POST /conversations/{id}/branches/select` with body `{ messageId }` switches the active branch and returns the new active path as `UIMessage[]`.
The AI Elements branch components already exist, unwired, in `src/components/ai-elements/message.tsx` (`MessageBranch`, `MessageBranchPrevious`, `MessageBranchNext`, `MessageBranchPage`).

Remaining steps:

- Add `selectBranch(conversationId, messageId)` to `src/lib/api.ts` that POSTs to `/conversations/{id}/branches/select` and returns `UIMessage[]`.
- Define a typed branch-metadata shape (e.g. in `src/lib/chat/`) and a guard that reads `message.metadata.branch` (it is `unknown` on `UIMessage`).
- In `MessageBubble.tsx` (and `parts/UserTextPart.tsx` / `MessagePart.tsx` for placement), render a prev/next selector showing `branchIndex + 1` of `branchCount` whenever `branchCount > 1`, on both user bubbles (edit forks) and assistant bubbles (regenerate forks), driven by which message carries `metadata.branch`.
- Wire the prev/next handlers in `ChatSidebar.tsx`: resolve the target sibling from `branch.siblingIds` plus the direction, call `selectBranch`, then `setMessages` with the returned path, and clear the fetched-documents panel. Disable the controls while streaming.
- Prefer a small presentational selector bound to `branchIndex` / `branchCount` over the existing `MessageBranch` context provider, which assumes it owns the alternative children rather than driving a server round-trip.

The live partial-response gap is unrelated and tracked in `backend/README.md`: a turn stopped mid-stream is not persisted on pydantic-ai 1.x, so it will not appear after a reload until the v2 upgrade.
