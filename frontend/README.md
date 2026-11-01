# Hivegent Frontend

Client-side decisions that are not obvious from the code.
The stack and transport are in [`../ARCHITECTURE.md`](../ARCHITECTURE.md), history, approval, and branch storage in [`../backend/README.md`](../backend/README.md).

## The client names conversations and messages

The client mints every conversation id and user message id as a UUID, and the server stores them under those ids.
A new chat is `/conversations/<fresh uuid>`, `/` redirects there, and its first turn to `POST /conversations/{id}/chat` creates the conversation.
`GET /conversations/{id}/messages` answers `[]` for an id no conversation holds, so a new chat loads like any other.
Another user's id answers 404, which the route loader turns into the not-found screen (`orNotFound`).

The route loader reads the stored messages afresh on every visit, validated by `validateUIMessages` with the metadata and data part schemas of `lib/chat/message.ts` (tool parts stay untyped).
`ChatLayout` is keyed by the conversation id and creates one `Chat` from them (`useHivegentChat`), which owns the messages from then on and stops when the conversation is left.

The backend owns history as a message tree.
The transport sends only the new message (none for a regenerate or a compaction) plus `trigger` and `messageId`.
An edit or a retry sends the replacement under a fresh id with `messageId` naming the replaced message (`resendOf`), since a reused id answers 409.
Editing or regenerating forks a sibling branch server-side, but there is no UI to navigate branches yet.

## The chat is read from its context

`ChatProvider` (`components/chat/chat-context.tsx`) provides the `Chat` and the conversation id, so components read what they need instead of receiving status, messages, and callbacks as props.
`MessageList` and the debug console subscribe to every chunk with `useChat`, everything else derives a value with `useChatSelector`, which renders again only once that value changes.
`useLive(message)` tells where a message stands, `streaming` while the chat streams into it, `waiting` while it is last and the chat is idle, and `stale` otherwise, and `MessageLiveProvider` hands it to the parts below.
The pure logic lives in `lib/chat`: `toolLineState` and `Live` in `tool-part.ts`, `partLayout` and `blocksOf` in `layout.ts`, and `liveOf`, `placeCompactions`, and `shouldContinueRun` in `chat-utils.ts`.

## AI Elements are composed, not vendored

The chat composes the shadcn/ui AI Elements patterns in place from `components/ui` (`Message`, `Trace`, `CodeBlock`, `Item`, `Questionnaire`, ...).
The components use Base UI, so triggers take a `render` element instead of `asChild`.
`npm run shadcnup` refreshes the installed components only, so remove a file from `components/ui` to keep it and its dependencies out.
The CLI's Base UI variants and animations are vendored unchanged as `src/shadcn.css`, so refresh it alongside from the URL in its header.

`blocksOf` (`lib/chat/layout.ts`) sorts a reply's parts into steps and blocks.
Each run of steps between two blocks is one Chain of Thought that opens to its `Trace`.
Every step is a trace line that expands to its detail, including subagents, changesets, and the steering queue.
An approval or `ask_user` question is a block, an `Item` outside the trace, until it is answered or a later message abandons it.
The live state decides whether a line still runs, so a stopped call and an abandoned prompt read as stopped.
A failed or stopped step is routine for an agent, so its line stays muted and only says so, with the error in its detail, while red is left to errors that end the run.
While nothing on screen shows the model working, a "Processing" line closes the running trace or stands where the next one starts (`isProcessing`).
Only the last user message is a scroll anchor, since the scroller jumps to the first anchor it never scrolled to whenever one item swaps for another.

Replies render with Streamdown, but fenced code goes to the shared `CodeBlock` (`MarkdownCode` in `chat/markdown/markdown-text.tsx`).
It uses the Shiki core with the JavaScript regex engine and the languages in `lib/highlighter.tsx`, which avoids a chunk per language and the Oniguruma WASM.
Mermaid is imported lazily on the first diagram.

Workspace documents render with markdown-to-jsx, since Streamdown is too slow for documents of hundreds of pages.
They are styled by shadcn/typeset, vendored unchanged as `src/typeset.css`, so refresh it from the URL in its header.
Replies keep Streamdown's own styles, since it has no unstyled mode.

## Server state lives in TanStack Query, client state in Zustand

Each resource is a module in `src/api` with its fetchers, its zod schemas, and its `<resource>Queries` and `<resource>Mutations`, built on the HTTP core in `api/client.ts`.
Every query is keyed under a scope that `Register` restricts (`lib/query-client.ts`), and every mutation that changes one names the keys it affects in `meta.invalidates`.
Routes load their data in `loader` through the `queryClient` of the router context, after `enforceLogin`, and components read it with `useSuspenseQuery`.
The mutation cache refetches those before the mutation settles and raises a toast on failure unless the mutation renders it (`meta.inlineError`) or has its own `onError`, so components neither refetch by hand nor copy server data into state.
Zustand keeps what the browser owns: settings, overrides, chunks the model read, and dialog state.
The composer store holds the agent mode and reasoning effort, which carry over from one conversation to the next, while the draft lives in the composer.

Two stores still hold server data, since neither fits a query: the jobs store is fed by the event stream, and the upload queue tracks progress of requests in flight.
Every workspace change the feed reports refetches the workspace queries (`syncWorkspaceQueries`).
A directory tree read walks the whole workspace, so `refreshTrees` never cancels a read in flight but marks it stale, and the read repeats once for any burst of changes.
Tree mutations publish their change rather than invalidating, and stay pending until the refreshed tree lands, so the rows they touch spin until then.

## Chat settings ride on every request

`prepareSendMessagesRequest` stamps the chat settings (`chatSettings`: document scope, model, tools, personality, mode) onto every request.
The SDK issues the post-approval continuation itself, so per-call settings would resume that turn with server defaults.

## Compaction is a part of the reply

A summary that replaces the earlier history arrives as a `data-compaction` part of an assistant message, live and after a reload.
`CompactionPart` renders it as a divider that opens to the summary.
The backend compacts and retries on its own when a turn overflows the context window, so the part can also appear in the middle of a reply.

A compaction made for a prompt is stored ahead of it, so `placeCompactions` moves one that opens a reply to the end of the reply before that prompt, live and in the SDK state once the stream finishes.

The compact button sends a request without a message, whose per-call body adds `compact: true` (`compact` in `hooks/chat/use-chat-actions.ts`).
It is disabled while an approval or question is open, which a compaction would leave unanswerable.
The SDK streams the response into the last reply, or into a new one after a failed prompt, which `placeCompactions` moves ahead of that prompt, just as a reload shows it.
The chat status shows the wait, so compaction needs no progress indicator of its own.

## Tool approvals

The SDK dispatches a continuation only if the chat is idle when the decision is recorded.
The buttons therefore stay disabled while the message streams and only answer while it waits (`useApprovalBlockedReason`).

The prompt is derived from stored history, since a tool call without a result is an open approval.
An approval overtaken by a new message reads as stopped, since its message is stale, and the backend declines it on the next request.

The changeset summary arrives as message metadata keyed by tool call id, live and after a reload.
`ApprovalScope` provides it, and `ToolCard` reads it with `useApprovalMetadata(part, schema)`.
`ChangesetView` (`components/chat/tools/changeset.tsx`) lists each change as a line that opens to its diff, and `run-python.tsx` reuses it for staged programs.
A `/tmp` file is read through the document route with the conversation id from `useConversationId`, none outside a conversation.

Every gated call asks with `ApprovalPrompt` (`components/chat/tools/approval.tsx`): input, changeset, an optional note, approve and deny.
A lone request is sent on click.
For several requests in one response, the buttons only stage a choice, and an `ApprovalBar` adds a shared note, "Approve all", "Deny all", and a submit.
`useApprovalRoundState` keys the staged state by request id, and `decide` records the whole batch, so the SDK sends one continuation.

A note travels as the decision's `reason` for both approvals and denials, and the backend records it.

## Questions are answered in their prompt

`ask_user` has no server-side body, so its prompt (`components/chat/tools/ask-user.tsx`) answers with `addToolOutput` through the same gate as approvals.
It composes the shadcn/ui `Questionnaire`, one step per question, and sends all answers from the last step.
`shouldContinueRun` (`lib/chat/chat-utils.ts`) resumes the run, instead of `lastAssistantMessageIsCompleteWithToolCalls`, which would also resend a stopped step of server tools.
The prompt shows once the run waits for its answers.
A dismissal is an `output-error`, and an overtaken question reads as stopped like an approval.

## Replies transfer to RAGold through its tab

`RagoldAction` (`components/chat/ragold-dialog.tsx`) offers the last text of a reply to RAGold when `/settings` names a `ragold_url`.
`draftFromTurn` (`lib/ragold.ts`) prefills the entry from the user turn, the reply without its tags, and the lines each citation names, resolved from the captured tool outputs like a citation chip (`lib/citations.ts`).
Documents are added from those outputs or from the workspace trees through a combobox that renders a bounded number of matches, and a passage is the text selected in the document's markdown, where the dialog stays open so several passages of one document are added in one go.
The transfer ships a complete entry, so nothing is preselected: the question type, the answerability, and every grade start unset, a selected passage or key fact is added through one button per grade or importance, and key facts are marked by selecting text of the response.
`completeEntry` turns the draft into the request only once everything is picked and every key fact still appears in the response, and the transfer stays disabled until then.
RAGold keeps its data in the browser, so Hivegent cannot upload to it: the click opens `<ragold_url>#import` synchronously, which popup blockers allow, while the backend builds the zip.
`sendToRagold` waits for `ragold:ready` from that tab and origin, posts `ragold:import` with the zip, and reports `ragold:result`, failing when the tab closes, stays silent for 30 seconds, or the zip fails, which also closes the tab.

## Interface language

All text lives in typed catalogs under `src/i18n/locales/{en,de}`, read with the react-i18next selector API (`t(($) => $.chat.composer.placeholder)`).
Each German section is declared `satisfies Translation<typeof en>`, so a missing or extra key fails the type check.
Both catalogs are bundled statically.

The language is the first supported entry of `navigator.languages`, fixed at page load, with no picker.
`authFetch()` sends it as `Accept-Language` on every request.
Dates, numbers, and sizes go through `@/i18n/format`, and `npm run i18n:lint` lists hardcoded JSX text.

## Licenses ship with the bundle

Every browser receives a copy of the bundle, so the licenses travel with it.
`vite.licenses.ts` writes the bundled packages' notices to `licenses.txt`, after the app's own license and terms, linked from the landing page and the user menu.
A plugin in `vite.config.ts` prefixes it with the project's license and additional terms from the repo root, the latter in their canonical German version.
It walks the JS module graph, so CSS has to be imported from JS to be listed, like the Inter font in `main.tsx`.

## Originals preview in a tab

`PreviewTabs` (`components/documents/preview-tabs.tsx`) shows every document, uploaded image, and extracted asset of the document dialog the same way, a Preview tab with the original and a Text tab with its markdown or plain text projection.
The tabs only appear when the original has a renderer, picked by `previewKind` (`lib/original-preview.ts`) from its media type, else its extension, and a highlighted chunk opens on the text.
Images and videos load through the asset route as before.
A PDF goes to the browser's own viewer in an unsandboxed `iframe`, since Chromium refuses PDFs in sandboxed frames, on an object URL retyped as `application/pdf`, so no pdf.js is bundled.
A DOCX is converted by `mammoth` and sanitized by `dompurify`, and an XLSX is read by `read-excel-file` into a plain table of its first 500 rows, one sheet at a time.
Only the active tab mounts, so `documentQueries.original` fetches the original from `/documents/original` only once the preview is opened, and the parsers are imported dynamically, keeping them out of the main bundle.

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

- Add a branch query to `src/api/conversations.ts` and `branch` to the metadata schema in `lib/chat/message.ts`.
- Render a prev and next selector in `MessageFooter` when `branchCount > 1`, on user and assistant bubbles.
- On selection, load the branch and `setMessages`, disabled while streaming.
- Send the continuation anchor when the user continues, edits, or regenerates on a viewed branch.
