# Hivegent Frontend

Client-side decisions that are not obvious from the code.
The stack and transport are in [`../ARCHITECTURE.md`](../ARCHITECTURE.md), history, approval, and branch storage in [`../backend/README.md`](../backend/README.md).

## The client names conversations and messages

The client mints every conversation id and user message id as a UUID, and the server stores them under those ids.
A new chat is `/conversations/<fresh uuid>`, `/` redirects there, and its first turn to `POST /conversations/{id}/chat` creates the conversation.
`GET /conversations/{id}/messages` answers `[]` for an id no conversation holds, so a new chat loads like any other.
Another user's id answers 404, which the route loader turns into the not-found screen (`orNotFound`).

The route loader reads the stored messages afresh on every visit, validated by `validateUIMessages` with the metadata and data part schemas of `lib/chat/message.ts` (tool parts stay untyped).
The `Chat` validates streamed messages with the same schemas, though the SDK passes a streamed data part it has no schema for, so the backend withholds every data part these schemas do not declare.
`ChatLayout` is keyed by the conversation id and creates one `Chat` from them (`useHivegentChat`), which owns the messages from then on and stops when the conversation is left.

The backend owns history as a message tree.
The transport sends only the new message (none for a regenerate or a compaction) plus `trigger` and `messageId`.
An edit or a retry sends the replacement under a fresh id with `messageId` naming the replaced message (`resendOf`), since a reused id answers 409.
Editing or regenerating forks a sibling branch server-side, but there is no UI to navigate branches yet.

## The chat is read from its context

`ChatProvider` (`components/chat/chat-context.tsx`) provides the `Chat` and the conversation id, so components read what they need instead of receiving status, messages, and callbacks as props.
`MessageList` and the debug console subscribe to every chunk with `useChat`, everything else derives a value with `useChatSelector`, which renders again only once that value changes.
`useLive(message)` tells where a message stands, `streaming` while the chat streams into it, `waiting` while it is last and the chat is idle, and `stale` otherwise, and `MessageLiveProvider` hands it to the parts below.
The pure logic lives in `lib/chat`: `toolLineState` and `Live` in `tool-part.ts`, `partLayout` and `blocksOf` in `layout.ts`, and `liveOf`, `settleTurn`, and `shouldContinueRun` in `chat-utils.ts`.

## Every turn ends as a reload shows it

The backend's projection of the stored messages is the one transcript, see "The transcript is one projection of the tree" in `../backend/README.md`.
A turn's stream ends on a transient `data-turn` part carrying that stored turn, which `useHivegentChat` swaps in for what streamed (`settleTurn`), so ids, steps, and states are those of a reload, and a message queued meanwhile stays after it.
`turn-parity.test.ts` replays the golden turns the backend records and checks that the swap changes nothing on screen.

A turn runs on at the server, so leaving the chat, reloading, or closing the tab only stops following it, and nothing asks before leaving.
Only messages queued to steer a running turn live in the tab alone, so while any wait, leaving the conversation or the page asks first (`useSteeringQueue`).
A reload follows the running turn again, and the history lists a conversation whose first turn still runs, so opening it there follows the turn too.

## AI Elements are composed, not vendored

The chat composes the shadcn/ui AI Elements patterns in place from `components/ui` (`Message`, `Trace`, `CodeBlock`, `Item`, `Questionnaire`, ...).
The components use Base UI, so triggers take a `render` element instead of `asChild`.
`npm run shadcnup` refreshes the installed components only, so remove a file from `components/ui` to keep it and its dependencies out.
The CLI's Base UI variants and animations are vendored unchanged as `src/shadcn.css`, so refresh it alongside from the URL in its header.

`blocksOf` (`lib/chat/layout.ts`) sorts a reply's parts into steps and blocks.
Each run of steps between two blocks is one Chain of Thought that opens to its `Trace`.
Every step is a trace line that expands to its detail, including subagents, changesets, and the steering queue.
A delegation's line stays closed until the user opens it to its result, the report of a task the agent waited for or the receipt of a background one, so a batch of delegations leaves the chat compact, and it links to the Tasks tab of the canvas, which shows both alike.
The Tasks tab (`components/chat/tasks/tasks-panel.tsx`) lists the conversation's tasks, newest first, each a line that opens to its subagent's run, rendered with the reply's own parts and chains of thought, and stops a running one.
The feed's `conversation-activity` events keep it and its open transcripts current.
When the feed announces a new turn, an open chat follows it with `resumeStream` as soon as its current stream settles (`useHivegentChat`).
An approval or `ask_user` question is a block, an `Item` outside the trace, until it is answered or a later message abandons it.
The live state decides whether a line still runs, so a call a stop cut off reads as stopped, and an abandoned prompt as the backend closes it, an approval as denied and a question as failed.
A failed or stopped step is routine for an agent, so its line stays muted and only says so, with the error in its detail, while red is left to errors that end the run.
While nothing on screen shows the model working, a "Processing" line closes the running trace or stands where the next one starts (`isProcessing`).
Only the last user message is a scroll anchor, since the scroller jumps to the first anchor it never scrolled to whenever one item swaps for another.

Replies render with Streamdown, but fenced code goes to the shared `CodeBlock` (`MarkdownCode` in `chat/markdown/markdown-text.tsx`).
It uses the Shiki core with the JavaScript regex engine and the languages in `lib/highlighter.tsx`, which avoids a chunk per language and the Oniguruma WASM.
Mermaid is imported lazily on the first diagram.
Model output is untrusted, since a prompt injection can make it spell a URL that carries conversation data.
So raw HTML in a reply renders only the elements markdown itself produces plus `<cite>` and `<imgref>` (`rehypePlugins` in `chat/markdown/config.ts`), an image keeps only its alt text, and pictures come from `<imgref>`, which fetches workspace assets from the API.
Links keep Streamdown's confirmation before they open.
The sanitizer prefixes ids and names with `user-content-`, so model output cannot clobber globals of the page.
`csp.json` holds the Content-Security-Policy, which the build adds to `index.html` as a meta tag and `nix/vhost.nix` sends as a header together with `frame-ancestors` and the identity provider's origin.
It allows scripts only from the app's own files, so the theme script lives in `public/theme.js`, while styles stay `'unsafe-inline'` for Mermaid's SVG and `object-src` allows `blob:` for Chrome's PDF viewer in the preview.
Zod's probe for `new Function` is refused and reported once, after which it parses without it.
oidc-spa hands scripts placeholders instead of tokens, which only requests to the app's own origin swap back, and freezes the browser APIs that do so (`vite.config.ts`).
DPoP stays off, since the backend accepts bearer tokens only.

Workspace documents render with markdown-to-jsx, since Streamdown is too slow for documents of hundreds of pages.
They mount their blocks progressively, a group of them per frame (`useProgressive`), so a document of thousands of chunks opens without blocking the page.
They are not virtualized, since their blocks differ in height, links within the page need their targets, and the browser's search needs the text.
Long lists of uniform rows, the document tree and the chunks beside a document, mount only the rows in and near view (`VirtualList`), keeping the focused row mounted so the keyboard moves on from it.
They are styled by shadcn/typeset, vendored unchanged as `src/typeset.css`, so refresh it from the URL in its header.
Their links open in a new tab, except those within the page, so a document never replaces the app.
Replies keep Streamdown's own styles, since it has no unstyled mode.

## The React Compiler memoizes

`@vitejs/plugin-react` runs the Rust port of the React Compiler (`oxc-transform-react`), so components and hooks carry a hand-written `useMemo`, `useCallback`, or `memo` only where the compiler falls short.
The compiler ignores TypeScript types, so it treats every value it does not create as mutable and takes every call on it, a method call included, as a possible mutation.
A value whose last possible mutation comes after a hook call is not memoized, so hooks come first and derived values after them.
Hook results are immutable to the compiler, which is why a hook that derives data around another one splits off a small hook of its own, such as `useTreeIndex` or `useDocumentContent`.
`MessageBubble` stays wrapped in `memo`, since the compiler caches the elements of a list as a whole, and the message list changes with every streamed chunk.
`TreeRow` does too, and opts out of the compiler with `"use no memo"`, since it reads the mutable tree item while rendering.
It takes its place in the tree as values, since the tree rebuilds its meta objects with every change, which would render every row again.
An explicit `useMemo` remains only for the search index in `useFuzzySearch`, since the compiler takes a search for a mutation of the index and would rebuild it with every keystroke.
Setting `compiler: { logDiagnostics: true }` in `vite.config.ts` lists the components and hooks it skips, currently the generated toast and `VirtualList`, whose virtualizer is a mutable instance.

## Server state lives in TanStack Query, client state in Zustand

Each resource is a module in `src/api` with its fetchers, its zod schemas, and its `<resource>Queries` and `<resource>Mutations`, built on the HTTP core in `api/client.ts`.
The zod schemas are written by hand, so `contract.test.ts` holds every enum and constant they mirror against the backend's JSON Schemas, and every tool name the backend registers against an icon and a label in each language, both from the contract `backend/tests/unit/test_contract.py` records.
Every query is keyed under a scope that `Register` restricts (`lib/query-client.ts`), and every mutation that changes one names the keys it affects in `meta.invalidates`.
Routes load their data in `loader` through the `queryClient` of the router context, after `enforceLogin`, and components read it with `useSuspenseQuery`.
The mutation cache refetches those before the mutation settles and raises a toast on failure unless the mutation renders it (`meta.inlineError`) or has its own `onError`, so components neither refetch by hand nor copy server data into state.
Zustand keeps what the browser owns: settings, chunks the model read, and dialog state.
The agent mode, reasoning effort, chat model, and personal instructions are the user's preferences, which the server keeps (`api/preferences`), so a choice applies to every conversation and client at once, set optimistically and reconciled with what the server stored, while the draft lives in the composer and the expanded folders in memory alone (`settings-store`), since they name private documents.

Binary previews such as asset images and PDF originals are queries too, whose data is an object URL flagged `meta.objectUrl`, so every view of one file shares a URL and the cache revokes it once the entry drops or holds a new one.
A conversation's stored messages load through `conversationQueries.messages` without being kept, since the chat owns them once it starts.

Two stores still hold server data, since neither fits a query: the jobs store is fed by the event stream, and the upload queue tracks progress of requests in flight.
An upload the rate limit refused waits for its `Retry-After` and goes again, holding its slot in the queue.
An uploaded or replaced document sends its file's `lastModified` as `modified_at`, and a collection zipped in the browser dates each entry the same way, a dropped archive's entries keeping their own date, since JSZip writes the DOS date in UTC as the backend reads it.
Every workspace change the feed reports refetches the workspace queries (`syncWorkspaceQueries`), except asset images, which refetch only for a path the change moved or deleted.
A directory tree read walks the whole workspace, so `refreshTrees` never cancels a read in flight but marks it stale, and the read repeats once for any burst of changes.
Tree mutations publish their change rather than invalidating, and stay pending until the refreshed tree lands, so the rows they touch spin until then.

## Chat settings ride on every request

A sent user message names its documents in its metadata (`selectionMetadata`), and `prepareSendMessagesRequest` adds only what addresses the turn, since the server applies the user's preferences.
The SDK issues the post-approval continuation itself, so per-call settings would resume that turn with server defaults.

The model is a catalog id from `GET /settings`, never an endpoint, and the composer offers what that model supports: the model selector only with more than one model, the reasoning effort only with the levels the model lists, and image attachments only for a vision model, at most its `max_images` per turn (`useChatModel`).
The server enforces each of these, so the composer only hides what would be refused.

## Compaction is a part of the reply

A summary that replaces the earlier history arrives as a `data-compaction` part of an assistant message, live and after a reload.
`CompactionPart` renders it as a divider that opens to the summary.
The backend compacts and retries on its own when a turn overflows the context window, so the part can also appear in the middle of a reply.

A compaction shows where it streamed, in the reply to the prompt it was made for, which is where the stored transcript shows it too.

The compact button sends a request without a message, whose per-call body adds `compact: true` (`compact` in `hooks/chat/use-chat-actions.ts`).
It is disabled while an approval or question is open, which a compaction would leave unanswerable.
The SDK streams the response into the last reply, or into a new one after a failed prompt, just as a reload shows it.
The chat status shows the wait, so compaction needs no progress indicator of its own.

## Tool approvals

The SDK dispatches a continuation only if the chat is idle when the decision is recorded.
The buttons therefore stay disabled while the message streams and only answer while it waits (`useApprovalBlockedReason`).

The prompt is derived from stored history, since a tool call without a result is an open approval.
An approval overtaken by a new message reads as denied, since its message is stale, and the backend denies it on the next request.

The changeset summary arrives as message metadata keyed by tool call id, live and after a reload, beside the arguments each call runs with once approved.
`ApprovalScope` provides both, and `ToolCard` reads the summary with `useApprovalMetadata(part, schema)` and shows the arguments of `useApprovalArgs(part)` in place of those the model sent, which the backend may have repaired.
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
A dismissal is an `output-error`, and an overtaken question reads as failed, as the backend dismisses it.

## Replies transfer to RAGold through its tab

`RagoldAction` (`components/chat/ragold-dialog.tsx`) offers the last text of a reply to RAGold when `/settings` names a `ragold_url`.
`draftFromTurn` (`lib/ragold.ts`) prefills the entry from the user turn, the reply without its tags, and the lines each citation names, resolved from the captured tool outputs like a citation chip (`lib/citations.ts`).
Documents are added from those outputs or from the workspace trees through a combobox that renders a bounded number of matches, and a passage is the text selected in the document's markdown, where the dialog stays open so several passages of one document are added in one go.
The transfer ships a complete entry, so nothing is preselected: the question type, the answerability, and every grade start unset, a selected passage or key fact is added through one button per grade or importance, and key facts are marked by selecting text of the response.
`completeEntry` turns the draft into the request only once everything is picked and every key fact still appears in the response, and the transfer stays disabled until then.
RAGold keeps its data in the browser, so Hivegent cannot upload to it: the click opens `<ragold_url>#import` synchronously, which popup blockers allow, while the backend builds the zip.
`sendToRagold` waits for `ragold:ready` from that tab and origin, posts `ragold:import` with the zip, and reports `ragold:result`, failing when the tab closes, stays silent for 30 seconds, or the zip fails, which also closes the tab.

## Deployment settings are read at runtime

Before rendering, `BootstrapGate` loads the public `/api/v1/config`, which names the OIDC client and the handbook URL, so one bundle serves every deployment without a rebuild.
No OIDC client means the backend runs as `hivegent dev` with its development user, which oidc-spa then mocks, and no handbook URL hides the "Documentation" link.
`runtimeConfig()` hands the loaded config to the components below the gate.

## The debugging consoles follow the server

The tool and chat debugger appear in the user menu and under `/debug` only when `/settings` reports `debug`, which the server sets for an admin of a deployment that enabled the consoles, and otherwise `/debug` shows the not-found screen.
The chat debugger opens only the user's own conversations, since the server finds them by the same ownership check as every conversation route.

## Settings and administration are routed pages

`/settings` holds every user's own pages, profile, instructions, skills, and account, and `/admin` the read-only view of what the operator configured, models, plugins, integrations, pipelines, global skills, plus the maintenance switch and the reindex.
Both are one `SettingsShell` with a side navigation over an `Outlet`, and their pages compose the same `Section` card and `DangerZone`, whose every action runs behind one confirmation.
The admin pages render for an admin only, as the `admin` role in `/settings` says, while the server refuses everyone else on every route, so the gate spares the rest only failed requests.
Nothing the server configures is spelled in the client: models, plugins, servers, pipelines, and skills all come from their listings.

## External MCP servers are read-only here

The host configuration declares the external MCP servers, so `/admin/integrations` only lists them (`GET /admin/integrations`) by kind, MCP servers, databases, and APIs, with each one's host, groups, and tools by effect, and an MCP server's status as well.

## Sources are listed, synced on demand

The host configuration declares the external sources, so `/admin/sources` lists them (`GET /admin/sources`) with their last sync, file count, and error, and its one action starts a sync (`POST /admin/sources/{id}/sync`), which the job tray follows like any job, since admins follow the jobs of every admin.
A source's mirror arrives as a workspace of kind `source` named by its id, so the tree, previews, and citations need nothing of their own, and `splitScopePath` takes `/mnt/<id>` whole as its scope.

## Skills are listed, never edited here

`/settings/skills` lists the skills of every scope the user sees (`GET /skills`), uploads a folder, zipped in the browser with the collection upload's helper, or a ZIP archive into the user's own library or a group's they write, and deletes one behind a confirmation.
A skill's `SKILL.md` opens in the document viewer like a `/tmp` file, through `GET /documents/{path}`, since a skill is no workspace entry and has no chunks.
The global skills come from the operator, so they get no upload or delete, and `/admin/skills` lists only them.
A draft that is a slash and one word suggests the skills it may invoke, and the server loads the skill named by the first word after a message's leading slash, so the client sends the text as typed.
A `library-changed` feed event refetches the skills wherever someone changed them.

## Instructions are a preference

`/settings/instructions` is one textarea over the `instructions` preference (`GET` and `PATCH /preferences`), which the server renders into every run at the user's authority, so the client sends no instructions with a chat request.
The legacy migration (`migrations/legacy-system-message.ts`) turns a custom system message a browser kept into these instructions once the user signs in there, only while they are empty.

## Interface language

Pipelines are ids the server lists for the admin pages with their label and description in the interface language (`GET /admin/pipelines/{stage}`), since plugins add their own, so no pipeline name is spelled in the client.
A stored pick the server no longer lists is sent as `auto` (`useOfferedPipeline`).

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

`PreviewTabs` (`components/documents/preview-tabs.tsx`) shows the original of a document in the document dialog, picked by `previewKind` (`lib/original-preview.ts`) from its media type, else its extension.
Where the text converts the original, as for a PDF, DOCX, or XLSX, the two are alternative renderings of the same content, so the original gets a Preview tab beside the Text tab, which a highlighted chunk opens on.
Where the text describes the original, as for an uploaded image or video and an extracted asset, the media stays pinned above its scrolling description without a tab, loaded through the asset route.
A PDF goes to the browser's own viewer in an unsandboxed `iframe`, since Chromium refuses PDFs in sandboxed frames, on an object URL retyped as `application/pdf`, so no pdf.js is bundled.
A DOCX is converted by `mammoth` and sanitized by `dompurify` without styles, forms, ids, or names, its links opening in a new tab, and an XLSX is read by `read-excel-file` into a plain table of its first 500 rows, one sheet at a time.
Only the active tab mounts, so `documentQueries.original` fetches the original from `/documents/original` only once the preview is opened, and the parsers are imported dynamically, keeping them out of the main bundle.

## The similarity of a case base

A document whose original may be a case base, as the server reports it by cbrkit's rule (`case_base_candidate` of its chunks), gets a Similarity tab in the document dialog (`components/documents/similarity-view.tsx`), loaded from `/case-bases` under the `workspace` query key, so the feed refreshes it with its workspace.
A folder the tree reports as a possible case base (`case_base_candidate`, `lib/document-tree.ts` `isCaseBaseFolder`) opens the same view in a dialog from its row in the document tree.
The view is read-only, since the server infers the configuration from the data and persists none while cbrkit's format still evolves (`components/documents/similarity/config-view.tsx`): every decision with its measure, weight, provenance, and reason, and each phase of the cycle (`retrieve`, `reuse`, `revise`, `retain`, `maintain`) as JSON or off.
The test query ranks by a record built by `SchemaForm` from the case base's query schema, by a case key, or by a graph file, and a graph match draws the case graph as Mermaid, its mapped nodes highlighted, through the chat's Streamdown plugin, loaded lazily.

## Moves and deletes are one request

Every move, delete, and new directory is one `applyChanges` request that the backend applies all or nothing.
Affected rows spin until their scope reloads, every scope the feed announces refreshes, and a refusal names the offending path.
A delete states whether it expects a document or a folder, so a stale view cannot delete the wrong kind.
Only rechunk and reconvert are background jobs, and both take a list of files.

## One headless tree spans every workspace

The documents panel is one `@headless-tree` instance (`hooks/use-document-tree.ts`), its invisible root holding a folder per workspace and every item keyed by canonical path, so a move between workspaces is a plain drop.
`lib/document-tree.ts` indexes the workspace trees into the items, and filters them to the fuzzy matches and their folders while searching.
The workspaces and whether each is writable come from the server (`user.workspaces` of `GET /settings`), in its scope order personal, groups, global, so the client never derives a grant, the admin's write on the global one included.
The selection drives both the bulk actions and the dragged entries, renames happen inline, and OS files dropped onto a folder upload into it.
A row's own controls leave the row alone, and a plain click on a file opens it, both handled by a small feature of the tree rather than by each control.
Tree mutations take canonical paths, so one request covers a selection spanning workspaces.
The focused item is the upload target, so no separate armed folder exists.
A fresh tree from the server rebuilds the item list during the render that brings it, since the expanded items handed to the tree are new with it, so no row of a vanished entry renders against the new data.
The selection and focus the tree gets leave out ids without an entry, which a kept path holds until the feed follows a change or while its workspace fails to load.
A folder toggled meanwhile keeps the persisted expansion of a workspace still loading or failing to, which the tree cannot know yet.
The tree is a mutable instance, so it comes from the React Compiler entrypoint of `@headless-tree/react` as a getter, and each row renders uncompiled, memoized on the item state its parent reads from the tree.

## Kept paths follow every workspace change

The document filter and the tree's selection, focus, expansion, and open document keep canonical paths that must follow what the backend applied.
Every change arrives once as the feed's `workspace-changed` event through `onWorkspaceChanged` in the jobs store, whoever made it, other tabs and group members included.
This tab's own change arrives from the answer to `POST /changes` as well, since the feed may be reconnecting, and the server-assigned id of the change lets whichever comes second pass, as applying a chain or swap twice would carry paths on.
The change drives every refresh, a tree mutation only joins its scope's read in flight, or starts one, so its rows spin until it lands.
`followPath` in `lib/utils` drops paths under a deleted one and carries paths under a moved source to the destination, all moves at once.
The conversation list follows the feed's `conversations-changed` event the same way (`syncConversationQueries`), once for a burst of events.
Its mutations refetch it too, and a rename writes the new title at once, since the feed may be reconnecting.

## TODO: branch-navigation UI

The backend keeps every branch in its `ConversationStore`: a conversation's record names its active history by `head_id`, and the tips of the histories an edit or a regenerate left in `branches`.

Backend:

- Answer the branches with `GET /conversations/{id}/messages`, each tip with the UI message it forks at.
- Switch the active history with the store's `branch(from_entry=...)`, which persists the choice and keeps the left history a branch.

Frontend:

- Render a prev and next selector in `MessageFooter` where a branch forks, on user and assistant bubbles.
- On selection, switch the branch and load its messages, disabled while streaming.

## TODO: virtualize the document tree

The tree renders every shown row, which stays cheap until an expanded workspace or a broad search shows thousands of them.
`@tanstack/react-virtual` would render only the rows in view, as headless-tree documents.

- Window `tree().getItems()` with `useVirtualizer` at a fixed row size, the rows being flat and of one height.
- Route the tree's `scrollToItem` through `scrollToIndex`, so keyboard navigation reaches rows out of view.
- Give the tree its own scroll container, or offset it by the header above it with `scrollMargin`, since both share the manager's scroll container today.
- Accept that find in page only sees the rendered rows.

No other list pays off yet: the conversation list wants server pagination with `useInfiniteQuery` first, the chat varies too much in height while it streams, and the rest stay small or capped.
