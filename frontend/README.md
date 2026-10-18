# Hivegent Frontend

Client-side decisions that are not obvious from the code.
The stack and transport are in [`../ARCHITECTURE.md`](../ARCHITECTURE.md), history, approval, and branch storage in [`../backend/README.md`](../backend/README.md).

## The client is not the source of chat history

The backend owns history as a message tree.
`prepareSendMessagesRequest` (`hooks/chat/use-hivegent-chat.ts`) sends only the new message (none for a regenerate) plus `trigger` and `messageId`.
History loads from `GET /conversations/{id}/messages`, where each `UIMessage.id` is a tree-node id.

A message sent in the current session starts with a local SDK id.
Every turn returns the stored node id in `X-Message-Id`, and `onFinish` swaps it in (`adoptMessageNodeId`).
It is read from the headers, so a stopped or failed turn still leaves an editable message.

Editing or regenerating forks a sibling branch server-side, but there is no UI to navigate branches yet.

## AI Elements are composed, not vendored

The chat composes the shadcn/ui AI Elements patterns in place from `components/ui` (`Message`, `Trace`, `CodeBlock`, `Item`, `Questionnaire`, ...).
The components use Base UI, so triggers take a `render` element instead of `asChild`.
`npm run shadcnup` installs or refreshes every registry component.
The CLI's Base UI variants and animations are vendored unchanged as `src/shadcn.css`, so refresh it alongside from the URL in its header.

`partLayout` (`chat/MessagePart.tsx`) sorts a reply's parts into steps and blocks.
Each run of steps between two blocks is one Chain of Thought that opens to its `Trace`.
Every step is a trace line that expands to its detail, including subagents, changesets, and the steering queue.
An approval or `ask_user` question is a block, an `Item` outside the trace, until it is answered.
`useMessageStreaming` decides whether a line still runs, so a stopped call reads as stopped.

Replies render with Streamdown, but fenced code goes to the shared `CodeBlock` (`MarkdownCode` in `chat/markdown/MarkdownText.tsx`).
It uses the Shiki core with the JavaScript regex engine and the languages in `lib/highlighter.tsx`, which avoids a chunk per language and the Oniguruma WASM.
Mermaid is imported lazily on the first diagram.

Workspace documents render with markdown-to-jsx, since Streamdown is too slow for documents of hundreds of pages.
They are styled by shadcn/typeset, vendored unchanged as `src/typeset.css`, so refresh it from the URL in its header.
Replies keep Streamdown's own styles, since it has no unstyled mode.

## Server state lives in TanStack Query, client state in Zustand

Every fetched resource is a `queryOptions` factory in `src/queries`, keyed under a scope that `Register` restricts, and every mutation that changes one names the keys it affects in `meta.invalidates`.
The mutation cache refetches those before the mutation settles and raises a toast on failure unless the mutation renders it (`meta.inlineError`) or has its own `onError`, so components neither refetch by hand nor copy server data into state.
Zustand keeps what the browser owns: settings, overrides, chunks the model read, and dialog state.

Two stores still hold server data, since neither fits a query: the jobs store is fed by the event stream, and the upload queue tracks progress of requests in flight.
Every workspace change the feed reports refetches the workspace queries (`syncWorkspaceQueries`).
A directory tree read walks the whole workspace, so `refreshTrees` never cancels a read in flight but marks it stale, and the read repeats once for any burst of changes.
Tree mutations publish their change rather than invalidating, and stay pending until the refreshed tree lands, so the rows they touch spin until then.

## Chat settings ride on every request

`prepareSendMessagesRequest` stamps the chat settings (document scope, model, tools, personality, mode) onto every request.
The SDK issues the post-approval continuation itself, so per-call settings would resume that turn with server defaults.

## Tool approvals

The SDK dispatches a continuation only if the chat is idle when the decision is recorded.
The buttons therefore stay disabled until the turn settles (`blockedReason` in `hooks/chat/use-tool-approval.ts`).

The prompt is derived from stored history, since a tool call without a result is an open approval.
An approval overtaken by a new message is declined on both sides: `chat-utils.closeAbandonedCalls` on the client, the backend on the next request.

The changeset summary arrives as message metadata keyed by tool call id, live and after a reload.
`ApprovalScope` provides it, and `ToolCard` reads it with `useApprovalMetadata(part, schema)`.
`ChangesetView` (`components/chat/tools/changeset.tsx`) lists each change as a line that opens to its diff, and `run-python.tsx` reuses it for staged programs.
A `/tmp` file is read through the document route with the conversation id from `useConversationId`.

Every gated call asks with `ApprovalPrompt` (`components/chat/tools/approval.tsx`): input, changeset, an optional note, approve and deny.
A lone request is sent on click.
For several requests in one response, the buttons only stage a choice, and an `ApprovalBar` adds a shared note, "Approve all", "Deny all", and a submit.
`useApprovalRoundState` keys the staged state by request id, and `decide` records the whole batch, so the SDK sends one continuation.

A note travels as the decision's `reason` for both approvals and denials, and the backend records it.

## Questions are answered in their prompt

`ask_user` has no server-side body, so its prompt (`components/chat/tools/ask-user.tsx`) answers with `addToolOutput` through the same gate as approvals.
It composes the shadcn/ui `Questionnaire`, one step per question, and sends all answers from the last step.
`chat-utils.shouldContinueRun` resumes the run, instead of `lastAssistantMessageIsCompleteWithToolCalls`, which would also resend a stopped step of server tools.
A dismissal is an `output-error`, and an overtaken question is dismissed like an approval.

## Interface language

All text lives in typed catalogs under `src/i18n/locales/{en,de}`, read with the react-i18next selector API (`t(($) => $.chat.composer.placeholder)`).
Each German section is declared `satisfies Translation<typeof en>`, so a missing or extra key fails the type check.
Both catalogs are bundled statically.

The language is the first supported entry of `navigator.languages`, fixed at page load, with no picker.
`getAuthHeaders()` sends it as `Accept-Language` on every request.
Dates, numbers, and sizes go through `@/i18n/format`, and `npm run i18n:lint` lists hardcoded JSX text.

## Moves and deletes are one request

Every move, delete, and new directory is one `applyChanges` request that the backend applies all or nothing.
Affected rows spin until their scope reloads, every scope in the returned `WorkspaceChanged` refreshes, and a refusal names the offending path.
A delete states whether it expects a document or a folder, so a stale view cannot delete the wrong kind.
Only rechunk and reconvert are background jobs, and both take a list of files.

## Kept paths follow every workspace change

The document filter, a scope's selection, and its open document dialog keep paths that must follow what the backend applied.
Every change arrives as one `WorkspaceChanged` through `onWorkspaceChanged` in the jobs store.
It comes from the `applyChanges` response for the acting tab (via `publishWorkspaceChanged`) and from the feed's `workspace-changed` event for everything else.
The SPA sends its client id, so each change arrives once.
`followPath` in `lib/utils` drops paths under a deleted one and carries paths under a moved source to the destination, all moves at once.
Group members other than the actor get no feed event yet.

## TODO: branch-navigation UI

Branch navigation is ephemeral client state.
The newest leaf is always active, and a choice becomes durable only when the user appends to that branch.
`GET /conversations/{id}/messages` already returns `metadata.branch = { branchCount, branchIndex, siblingIds }` on forking nodes.

Backend, read-only and without schema changes:

- Add `?branch={messageId}` to `GET /conversations/{id}/messages` to project the active path from a chosen leaf.
- Let a chat request carry a continuation anchor, so `resolve_fork` forks at the viewed branch instead of the newest.

Frontend:

- Add `loadBranch(conversationId, messageId)` to `src/lib/api.ts` and a typed guard for `message.metadata.branch`.
- Render a prev and next selector in `MessageFooter` when `branchCount > 1`, on user and assistant bubbles.
- On selection, load the branch and `setMessages`, disabled while streaming.
- Send the continuation anchor when the user continues, edits, or regenerates on a viewed branch.
