# Hivegent Frontend

The client-side decisions that are not obvious from the code.
The stack and the transport contracts are in [`../ARCHITECTURE.md`](../ARCHITECTURE.md), and everything about how history, approvals, and branches are stored is in [`../backend/README.md`](../backend/README.md).

## The client is not the source of chat history

The backend owns conversation history as a message tree, so the client never echoes the whole conversation.
`prepareSendMessagesRequest` in `src/hooks/chat/use-hivegent-chat.ts` sends only the new message (none for a regenerate) plus `trigger` and `messageId`, and the server loads the active-path prefix from its store and forks or appends under the node `messageId` addresses.
History is hydrated on load from `GET /conversations/{id}/messages` (`use-conversation-history.ts`), where each `UIMessage.id` is the server's tree-node id.

A message sent in the current session starts out with a local SDK id instead, which addresses nothing server-side, so every turn returns the node id it stored the user message under in `X-Message-Id` and `onFinish` swaps it in (`adoptMessageNodeId`).
The id is read off the response headers rather than the stream, so a turn the user stops or that errors still leaves an editable message behind.
Adopting it re-keys that bubble and therefore remounts it; the alternative, translating ids at send time, would leave in-session ids differing from post-reload ones and is not worth that.

Submit, edit, regenerate, retry, and stop work end-to-end.
Editing a message or regenerating a reply forks a sibling branch server-side and preserves the prior one, but the UI to navigate those branches is not built yet.

## Chat settings ride on every request

`prepareSendMessagesRequest` stamps the chat settings (document scope, model, tools, personality, mode) onto every request, rather than each call site attaching a body.
The SDK issues the post-approval continuation itself, with only the options the decision was recorded with, so settings attached per call would leave that one request bare and resume the turn with the server's defaults: no document scope, no model override, no MCP servers.
A per-call `body` still wins, which is how "Execute the plan" leaves the mode it is currently in.

## Tool approvals

A decision lives only in the browser's memory until the resumed turn is persisted, and the SDK dispatches the continuation only if the chat happens to be idle when the decision is recorded — it never looks again.
The buttons therefore stay disabled until the turn settles (`approvalBlockedReason` in `ChatSidebar.tsx`); without that, a decision taken while the last chunks drained would be recorded, never sent, and leave the call dangling for the backend to close.

The prompt itself is re-derived from stored history rather than stored as a decision, since a tool call with no result is an open approval.
An approval the user overtakes by sending another message is declined on both sides: `chat-utils.declineAbandonedApprovals` derives the denial over the live transcript while the backend closes the dangling call on the next request.

## Interface language

All text lives in typed catalogs under `src/i18n/locales/{en,de}`, one namespace split into sections, read with the react-i18next selector API (`t(($) => $.chat.composer.placeholder)`).
English is the source catalog whose literal types check keys and interpolation variables, and each German section is declared `satisfies Translation<typeof en>`, so a missing or extra key fails the type check instead of falling back at runtime.
The catalogs are bundled statically: two small languages cost less than a lazy-loading round trip and nothing ever suspends.

The browser alone decides the language: `LANGUAGE` is the first supported entry of `navigator.languages`, fixed at page load and never stored, so there is no picker and changing the browser language takes effect on the next load.
`getAuthHeaders()` sends it as `Accept-Language` on every request, so the backend answers in the language the interface shows rather than renegotiating from the browser's own header.
Dates, numbers, and sizes go through `@/i18n/format` rather than `toLocale*String()`, and `npm run i18n:lint` lists hardcoded JSX text.

## TODO: branch-navigation UI

The active branch is always the newest leaf and the backend stores no selection pointer, so **branch navigation is ephemeral client view state, never persisted server-side.**
Viewing an older branch just swaps the messages the client renders; the choice becomes durable only when the user _appends_ to that branch (continue, edit, or regenerate), because the appended chain is then the newest leaf the server already treats as active on the next load.
So there is **no** `/branches/select` endpoint, no `set_active_leaf`, and no schema change — only a read-only projection plus telling the server where a turn continues from.

The backend already emits the navigation data: `GET /conversations/{id}/messages` returns `UIMessage`s whose forking nodes carry `metadata.branch = { branchCount, branchIndex, siblingIds }`.
The AI Elements branch components already exist, unwired, in `src/components/ai-elements/message.tsx` (`MessageBranch`, `MessageBranchPrevious`, `MessageBranchNext`, `MessageBranchPage`).

Backend additions, both read-only and with no new column:

- Give `GET /conversations/{id}/messages` an optional `?branch={messageId}` that anchors the active-path projection at a chosen leaf: descend the addressed node to its branch tip and walk up to the root (the same `_load_active_path` / `dump_messages_with_ids` path, just a different anchor). Without the param it returns the newest branch as today.
- Let a turn continue a non-newest branch: the chat request carries the continuation anchor (the id of the last message in the client's current view) so `resolve_fork` forks there rather than at the global newest. Edit and regenerate keep their `messageId` semantics, and on the newest branch the anchor _is_ the newest leaf, so behaviour is unchanged.

Frontend steps:

- Add `loadBranch(conversationId, messageId)` to `src/lib/api.ts` that GETs `/conversations/{id}/messages?branch={messageId}` and returns `UIMessage[]`.
- Define a typed branch-metadata shape (e.g. in `src/lib/chat/`) and a guard that reads `message.metadata.branch`, which is `unknown` on `UIMessage`.
- In `MessageBubble.tsx` (and `parts/UserTextPart.tsx` / `MessagePart.tsx` for placement), render a prev/next selector showing `branchIndex + 1` of `branchCount` whenever `branchCount > 1`, on both user bubbles (edit forks) and assistant bubbles (regenerate forks).
- Wire the handlers in `ChatSidebar.tsx`: resolve the target sibling from `branch.siblingIds` plus the direction, call `loadBranch`, `setMessages` with the returned path, and clear the fetched-documents panel. Pure client view state, no server round-trip, disabled while streaming.
- When the user continues, edits, or regenerates while viewing a branch, include the continuation anchor in the chat request so the new chain forks off that branch and becomes the newest.
- Prefer a small presentational selector bound to `branchIndex` / `branchCount` over the existing `MessageBranch` context provider, which assumes it owns the alternative children rather than driving a server fetch.
