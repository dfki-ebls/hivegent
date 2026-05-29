# Frontend Wiring Tasks

Backend routes for all of the following already exist; only the frontend UI wiring is missing.

## A. Group document writes

The Zustand store `stores/group-documents-store.ts` (`useGroupDocumentsStore`) and the `lib/api.ts` functions it calls (`uploadGroupDocument`, `uploadGroupCollectionStream`, `deleteGroupDocument`, `createGroupDirectory`, `deleteGroupDirectory`) already exist and hit live backend routes in `backend/src/hivegent/server/routes/groups.py`, but no component consumes them.

In `components/DocumentCanvas.tsx`, the `GroupDocumentsSection` is currently rendered read-only (only `onInclude` / `onExclude` / `onViewFile` are passed; the optional `onRemoveFile` / `onCreateSubdir` / `onDeleteDir` props are never provided, and there is no upload affordance).

Mirror the user-document write path (the `useUserDocumentsStore` usage and the `onFileAction` / `onCreateSubdir` / `onDeleteDir` handlers around lines 1445–1466) to:

1. Drive `GroupDocumentsSection` from `useGroupDocumentsStore` (select the group via `selectGroup`).
2. Pass write handlers (`onRemoveFile`, `onCreateSubdir`, `onDeleteDir`) that call the store's `remove` / `createDir` / `deleteDir`, gated by `canWriteGroup(groupId)`.
3. Add a file-upload affordance to the section (file input → store `upload` / `uploadMultiple`), refreshing the directory tree afterward.
4. Confirm `canWriteGroup(groupId)` reflects the same permission the backend enforces.

## B. Single-shot document ops in `lib/api.ts`

These wrappers exist with live backend routes but no caller, because the UI currently uses the streaming variants (via the document store) instead:

- `reconvertDocument` → `POST /documents/reconvert/{path}` (UI uses `reconvert_document_stream`)
- `rechunkDocument` → `POST /documents/rechunk/{path}` (UI uses the store's streaming rechunk)
- `replaceOriginal` → `PUT /documents/original/{path}` (no UI to replace an original file)
- `uploadCollection` → `POST /documents/collections` (UI uses the stream variant)
- `uploadGroupCollection` → `POST /groups/{id}/documents/collections` (same)

Decide per-op whether the non-streaming path is wanted. `replaceOriginal` is the only one with no UI counterpart of any kind — it needs a "replace original file" action in the document menu if intended. The other four are redundant with their streaming siblings; either add a non-streaming call path or treat them as the future API and leave them.

## Verification

Run `oxlint --type-aware --type-check src` and `npx tsc --noEmit` when done.
