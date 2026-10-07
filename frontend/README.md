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

## Tool approvals

A decision lives only in the browser's memory until the resumed turn is persisted, and the SDK dispatches the continuation only if the chat happens to be idle when the decision is recorded — it never looks again.
The buttons therefore stay disabled until the turn settles (the gate's `blockedReason` in `ChatSidebar.tsx`).
Without that, a decision taken while the last chunks drained would be recorded, never sent, and leave the call dangling for the backend to close.

The prompt itself is re-derived from stored history rather than stored as a decision, since a tool call with no result is an open approval.
An approval the user overtakes by sending another message is declined on both sides: `chat-utils.declineAbandonedApprovals` derives the denial over the live transcript while the backend closes the dangling call on the next request.

The protocol's approval request carries ids only, so the changeset summary the planner resolved for the prompt arrives as message metadata keyed by tool call id, live and after a reload alike.
`MessageBubble` provides it per message and `ToolCard` reads its own call's entry with `useApprovalMetadata(part, schema)`, which validates it with zod.
Every approval that changes the workspace (a mutation tool or `apply_changes`) asks with that summary, so one `ChangesetView` (`components/chat/tools/changeset.tsx`) shows the real diffs for every tool, and `run-python.tsx` reuses it to show what a program staged.
A tool card reads a `/tmp` file through the same document route as a workspace one, passing the persisted conversation id that `ChatSidebar` provides through `useConversationId`, so a draft's `/tmp` script waits for its first turn to persist.
Every gated card asks with the same approve and deny buttons and an optional note for the assistant, and a lone request is sent on click.
Several requests of one response are the special case: the card buttons then only stage a choice (`aria-pressed`), and an `ApprovalBar` under the message adds a shared note, "Approve all" and "Deny all", and a submit that sends the staged choices once every call has one.
The fine-grained choices need no extra collapsible, since the cards themselves are collapsible and open while a decision is pending.
`MessageBubble` holds the staged state (`useApprovalRoundState`) keyed by request id, so a later round starts blank, and provides it to its cards.
The gate's `decide` takes the whole batch and records each decision in turn, and since the SDK continues only once the last one is in, that still sends a single continuation.

A note travels as the decision's `reason`, which is the user's own text on both decisions: the backend words the refusal the model reads around it for a denial and appends it to the result of an approved call (see `../backend/README.md`), and records it so the resolved card shows it live and after a reload alike.

## Interface language

All text lives in typed catalogs under `src/i18n/locales/{en,de}`, one namespace split into sections, read with the react-i18next selector API (`t(($) => $.chat.composer.placeholder)`).
English is the source catalog whose literal types check keys and interpolation variables, and each German section is declared `satisfies Translation<typeof en>`, so a missing or extra key fails the type check instead of falling back at runtime.
The catalogs are bundled statically: two small languages cost less than a lazy-loading round trip and nothing ever suspends.

The browser alone decides the language: `LANGUAGE` is the first supported entry of `navigator.languages`, fixed at page load and never stored, so there is no picker and changing the browser language takes effect on the next load.
`getAuthHeaders()` sends it as `Accept-Language` on every request, so the backend answers in the language the interface shows rather than renegotiating from the browser's own header.
Dates, numbers, and sizes go through `@/i18n/format` rather than `toLocale*String()`, and `npm run i18n:lint` lists hardcoded JSX text.

## Moves and deletes are one request

Every move, delete, and new directory, of one item or a whole selection, is one `applyChanges` request that the backend applies all or nothing.
The store sends it through one helper: the affected rows spin until their scope reloads, every scope the returned `WorkspaceChanged` names refreshes, and a refusal is one error naming the offending path.
A delete names whether it expects a document or a folder, so a stale view can never delete a folder in place of a document.
Only bulk rechunk and reconvert are background jobs with progress in the job tray, since they convert and index.

## Kept paths follow every workspace change

The chat's document filter, a scope's selection, and its open document dialog keep canonical or local paths, so one left at a stale path would silently stop hiding a document from the agent or point at nothing.
They follow what the backend applied, never what a client sent, since the backend resolves a move into an existing folder and moves an entry with its original.
Every change that never was a job says which scopes it touched and what it moved and deleted as one `WorkspaceChanged`, and `onWorkspaceChanged` in the jobs store is the one channel it reaches them through, fed by the `applyChanges` response that the documents store passes to `publishWorkspaceChanged` for the tab that asked, whose own feed skips the change, and by the feed's `workspace-changed` event for everything else, the agent, an MCP client, or another tab.
The SPA always sends its client id, so each change arrives once, and its scopes refresh the trees the documents store has loaded.
`followPath` in `lib/utils` drops a path at or below a deleted one, then carries one at or below a moved source over to its destination, all moves at once so swaps and chains land right, and the filter's `follow` and `useFollowedState`, behind the scope's selection and dialog, keep their state when nothing changed.
Group members other than the one who acted get no feed event yet, so their kept paths do not follow.

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
