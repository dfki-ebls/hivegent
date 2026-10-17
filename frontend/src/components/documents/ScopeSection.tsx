import { useMutation, useQuery } from "@tanstack/react-query";
import { FolderOpen, Users } from "lucide-react";
import { useCallback, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  PERSONAL_SCOPE,
  buildAuxLlmConfig,
  canonicalPath,
  downloadOriginal,
  splitScopePath,
} from "@/lib/api";
import type { TreeItemDrag } from "@/lib/dnd";
import type { DocumentActionId } from "@/lib/document-actions";
import type { PipelineSpec } from "@/lib/types";
import { downloadBlob } from "@/lib/download";
import {
  basename,
  collectFilePaths,
  commonParentDir,
  errorMessage,
  treeDocuments,
} from "@/lib/utils";
import { useDocumentFilterStore } from "@/stores/document-filter-store";
import {
  documentMutations,
  documentQueries,
  useMutatingPaths,
} from "@/queries/documents";
import { useSettingsStore } from "@/stores/settings-store";
import { DirectoryTreeView, TreeRow, type TreeRowDnd } from "@/components/DirectoryTreeView";
import { DocumentDialog } from "@/components/DocumentDialog";
import { EmptyState, LoadingState } from "@/components/EmptyState";
import { ErrorAlert } from "@/components/ErrorAlert";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Field, FieldLabel } from "@/components/ui/field";
import { ItemGroup } from "@/components/ui/item";
import { BulkActionBar } from "@/components/documents/BulkActionBar";
import { DocumentListItem } from "@/components/documents/DocumentListItem";
import {
  FilterToggleButtons,
  type FilterEntryState,
} from "@/components/documents/FilterToggleButtons";
import { ExpandButton, NameButton } from "@/components/RowButtons";
import { ScopeDialogs, type ScopeDialogsHandle } from "@/components/documents/ScopeDialogs";
import { type FollowLocal, followSet, useFollowedState } from "@/hooks/use-followed-state";
import { useFuzzySearch } from "@/hooks/use-fuzzy-search";

/** Where a workspace change moved the open document, null once it was deleted or left the scope. */
function followDialog(path: string | null, follow: FollowLocal): string | null {
  return path === null ? null : follow(path);
}

interface ScopeSectionProps {
  /** Workspace scope: `~` for personal, `@<group>` for a group. */
  scope: string;
  label: string;
  canWrite: boolean;
  /** Whether the section starts expanded (personal). */
  defaultOpen: boolean;
  searchQuery: string;
  pipelineSpec: PipelineSpec;
  /** Canonical directory currently armed as the upload/create target. */
  armedTarget: string;
  /** Arm a canonical directory as the target. */
  onArmTarget: (target: string) => void;
  /** Upload dropped OS entries into a canonical directory. */
  onUploadInto: (target: string, items: DataTransferItem[], files: File[]) => void;
}

/**
 * Self-contained document manager for one workspace scope. Used identically
 * for the personal store and each group, so the two share one code path. The
 * shared upload area (in DocumentManager) deposits into the selected scope, and
 * the job that settles refreshes the scope's tree query.
 */
export function ScopeSection({
  scope,
  label,
  canWrite,
  defaultOpen,
  searchQuery,
  pipelineSpec,
  armedTarget,
  onArmTarget,
  onUploadInto,
}: ScopeSectionProps) {
  const { t } = useTranslation();
  const selectAllId = useId();
  const { data: directoryTree, isPending, error } = useQuery(documentQueries.tree(scope));
  const mutatingPaths = useMutatingPaths(scope);
  const { mutate: rechunk } = useMutation(documentMutations.rechunk());
  const { mutate: reconvert } = useMutation(documentMutations.reconvert());
  const { mutate: move } = useMutation(documentMutations.move());
  const { mutateAsync: write } = useMutation(documentMutations.write());
  const overrides = useSettingsStore((s) => s.overrides);
  const included = useDocumentFilterStore((s) => s.included);
  const excluded = useDocumentFilterStore((s) => s.excluded);
  const toggleInclude = useDocumentFilterStore((s) => s.toggleInclude);
  const toggleExclude = useDocumentFilterStore((s) => s.toggleExclude);

  const documents = useMemo(
    () => (directoryTree ? treeDocuments(directoryTree.root) : []),
    [directoryTree],
  );

  const dialogs = useRef<ScopeDialogsHandle>(null);
  const [isOpen, setIsOpen] = useState(defaultOpen);
  // The target outlives a close, so the dialog keeps its content while it animates out.
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogPath, setDialogPath] = useFollowedState<string | null>(scope, null, followDialog);
  const [selectedFiles, setSelectedFiles] = useFollowedState(scope, new Set<string>(), followSet);
  const filtered = useFuzzySearch(documents, searchQuery, "display_name", "filename");

  const toCanonical = useCallback((path: string) => canonicalPath(scope, path), [scope]);
  const filterStateOf = useCallback(
    (canonical: string): FilterEntryState =>
      included.includes(canonical)
        ? "included"
        : excluded.includes(canonical)
          ? "excluded"
          : undefined,
    [included, excluded],
  );

  const openDocument = (path: string) => {
    setDialogPath(path);
    setDialogOpen(true);
  };

  const isSearching = searchQuery.trim().length > 0;
  const expanded = isOpen || isSearching;

  const docsByFilename = useMemo(() => new Map(documents.map((d) => [d.filename, d])), [documents]);

  const clearSelection = useCallback(() => setSelectedFiles(new Set()), [setSelectedFiles]);
  const toggleFile = useCallback(
    (path: string) => {
      setSelectedFiles((prev) => {
        const next = new Set(prev);

        if (next.has(path)) next.delete(path);
        else next.add(path);

        return next;
      });
    },
    [setSelectedFiles],
  );
  const toggleDirFiles = useCallback(
    (paths: string[]) => {
      setSelectedFiles((prev) => {
        const all = paths.every((p) => prev.has(p));
        const next = new Set(prev);

        for (const p of paths) {
          if (all) next.delete(p);
          else next.add(p);
        }

        return next;
      });
    },
    [setSelectedFiles],
  );

  const visibleFilePaths = useMemo(() => {
    if (isSearching) return filtered.map((d) => d.filename);

    return directoryTree ? collectFilePaths(directoryTree.root) : [];
  }, [isSearching, filtered, directoryTree]);

  const selectedVisible = visibleFilePaths.filter((p) => selectedFiles.has(p)).length;
  const allSelected = visibleFilePaths.length > 0 && selectedVisible === visibleFilePaths.length;

  const toggleSelectAll = () => {
    if (allSelected) clearSelection();
    else setSelectedFiles(new Set(visibleFilePaths));
  };

  const selectedReconvertable = useMemo(
    () => [...selectedFiles].filter((f) => docsByFilename.get(f)?.has_original === true),
    [selectedFiles, docsByFilename],
  );

  const handleReconvert = (paths: string[]) =>
    reconvert({ scope, paths, spec: pipelineSpec, llm: buildAuxLlmConfig(overrides) });

  // One at a time, so the browser never sees a burst of downloads.
  const { mutate: downloadOriginals } = useMutation({
    mutationFn: async (paths: readonly string[]) => {
      for (const path of paths) {
        const blob = await downloadOriginal(toCanonical(path));
        downloadBlob(blob, basename(docsByFilename.get(path)?.original_path ?? "") || "original");
      }
    },
  });

  const handleSave = useCallback(
    async (filename: string, content: string) => {
      const { scope: target, local } = splitScopePath(filename);
      await write({ scope: target, paths: [local], content, chunking: pipelineSpec.chunking });
    },
    [pipelineSpec, write],
  );

  const bulkHandlers: Record<DocumentActionId, () => void> = {
    rechunk: () => {
      const paths = [...selectedFiles];
      clearSelection();
      rechunk({ scope, paths, spec: pipelineSpec });
    },
    reconvert: () => {
      const files = [...selectedReconvertable];
      clearSelection();
      handleReconvert(files);
    },
    download: () => downloadOriginals(selectedReconvertable),
    delete: () => dialogs.current?.bulkDelete([...selectedFiles]),
  };

  // Resolve a dragged row or selection into one move. The drag originates in
  // `drag.scope` and lands in this section's `scope`, the same workspace for an
  // in-place move, a different one when migrating between the personal and a
  // shared space (the drop only fires for a valid move). The dragged paths keep
  // their structure relative to their common parent, so a single file or
  // directory keeps its name under the destination.
  const handleMoveInto = useCallback(
    (drag: TreeItemDrag, destDir: string) => {
      const commonParent = commonParentDir(drag.paths);
      const destination = (source: string) => {
        const suffix = source.slice(commonParent.length);
        return destDir ? `${destDir}/${suffix}` : suffix;
      };
      const sameScope = drag.scope === scope;
      // Within one workspace a same-path entry is a no-op; across workspaces
      // it still re-homes the entry, so keep it.
      const paths = drag.paths.filter((source) => !sameScope || destination(source) !== source);

      if (drag.paths.length > 1) clearSelection();

      if (paths.length > 0) move({ scope: drag.scope, paths, destScope: scope, destination });
    },
    [scope, move, clearSelection],
  );

  // Local dir armed within this scope, or null when the target is elsewhere.
  const armed = splitScopePath(armedTarget);
  const armedHere = armed.scope === scope ? armed.local : null;

  // The header is the scope-root drop target, a sibling of the tree and never
  // an ancestor of its rows, so root and per-folder targets never nest.
  const rootDnd: TreeRowDnd = {
    scope,
    resolveDrag: null,
    destDir: canWrite ? "" : null,
    onMove: (drag) => handleMoveInto(drag, ""),
    onUpload: (items, files) => onUploadInto(scope, items, files),
  };

  const treeView = () => {
    // Read off the rendered tree rather than the response totals, so an
    // optimistically grafted directory counts immediately.
    if (!directoryTree?.root.children?.length) {
      return <EmptyState description={t(($) => $.documents.scope.empty)} className="p-4" />;
    }

    return (
      <DirectoryTreeView
        entry={directoryTree.root}
        scope={scope}
        mutatingPaths={mutatingPaths}
        onEditFile={openDocument}
        onInclude={(path) => toggleInclude(toCanonical(path))}
        onExclude={(path) => toggleExclude(toCanonical(path))}
        filterState={(path) => filterStateOf(toCanonical(path))}
        onDeleteFile={canWrite ? (path) => dialogs.current?.deleteFile(path) : undefined}
        onDeleteDir={canWrite ? (path) => dialogs.current?.deleteDir(path) : undefined}
        onRenameFile={canWrite ? (path) => dialogs.current?.renameFile(path) : undefined}
        onRenameDir={canWrite ? (path) => dialogs.current?.renameDir(path) : undefined}
        selectedFiles={canWrite ? selectedFiles : undefined}
        onToggleSelectFile={canWrite ? toggleFile : undefined}
        onToggleSelectDir={canWrite ? toggleDirFiles : undefined}
        armedDir={armedHere}
        onArm={canWrite ? (localDir) => onArmTarget(canonicalPath(scope, localDir)) : undefined}
        onMoveInto={canWrite ? handleMoveInto : undefined}
        onUploadInto={
          canWrite
            ? (destDir, items, files) => onUploadInto(canonicalPath(scope, destDir), items, files)
            : undefined
        }
      />
    );
  };

  const flatList = () => {
    if (filtered.length === 0) {
      return <EmptyState description={t(($) => $.documents.scope.noMatches)} className="p-4" />;
    }

    return (
      <ItemGroup>
        {filtered.map((doc) => (
          <DocumentListItem
            key={doc.filename}
            doc={doc}
            isMutating={mutatingPaths.has(doc.filename)}
            onEdit={() => openDocument(doc.filename)}
            filterState={filterStateOf(toCanonical(doc.filename))}
            onIncludeDocument={() => toggleInclude(toCanonical(doc.filename))}
            onExcludeDocument={() => toggleExclude(toCanonical(doc.filename))}
            onRemove={canWrite ? () => dialogs.current?.deleteFile(doc.filename) : undefined}
            selected={selectedFiles.has(doc.filename)}
            onToggleSelect={canWrite ? () => toggleFile(doc.filename) : undefined}
          />
        ))}
      </ItemGroup>
    );
  };

  const fileCount = directoryTree?.total_files ?? 0;
  const ScopeIcon = scope === PERSONAL_SCOPE ? FolderOpen : Users;

  // In a writable scope the label arms the root (and opens the section),
  // otherwise it is a plain collapse trigger.
  const labelButton = canWrite ? (
    <NameButton
      title={t(($) => $.documents.scope.setUploadTarget)}
      onClick={() => {
        onArmTarget(scope);
        setIsOpen(true);
      }}
    >
      {label}
    </NameButton>
  ) : (
    <CollapsibleTrigger render={<NameButton />}>{label}</CollapsibleTrigger>
  );

  // The document the dialog is open on, resolved once for the write-action gates below.
  const dialogDoc = dialogPath === null ? undefined : docsByFilename.get(dialogPath);

  return (
    <Collapsible open={expanded} onOpenChange={setIsOpen}>
      <TreeRow dnd={rootDnd} isArmed={armedHere === ""}>
        <CollapsibleTrigger render={<ExpandButton expanded={expanded} />} />
        <ScopeIcon className="size-4 shrink-0 text-muted-foreground" />
        <div className="flex min-w-0 flex-1">{labelButton}</div>
        <div className="flex gap-0.5">
          <FilterToggleButtons
            state={filterStateOf(scope)}
            onInclude={() => toggleInclude(scope)}
            onExclude={() => toggleExclude(scope)}
            revealOnHover
          />
        </div>
        <Badge variant="secondary">{fileCount}</Badge>
      </TreeRow>

      <CollapsibleContent className="flex flex-col gap-1 py-1">
        {error && <ErrorAlert>{errorMessage(error)}</ErrorAlert>}
        {isPending ? (
          <LoadingState className="p-6" />
        ) : (
          <>
            {canWrite && selectedFiles.size > 0 ? (
              <BulkActionBar
                selectedCount={selectedFiles.size}
                hasReconvertable={selectedReconvertable.length > 0}
                handlers={bulkHandlers}
                onClear={clearSelection}
              />
            ) : (
              canWrite &&
              !isSearching &&
              fileCount > 0 && (
                <Field orientation="horizontal" className="min-h-9 px-2">
                  <Checkbox
                    id={selectAllId}
                    checked={allSelected}
                    indeterminate={selectedVisible > 0 && !allSelected}
                    onCheckedChange={toggleSelectAll}
                  />
                  <FieldLabel htmlFor={selectAllId} className="font-normal text-muted-foreground">
                    {t(($) => $.documents.scope.selectAll)}
                  </FieldLabel>
                </Field>
              )
            )}
            {isSearching ? flatList() : treeView()}
          </>
        )}
      </CollapsibleContent>

      <DocumentDialog
        open={dialogOpen && dialogPath !== null}
        onOpenChange={setDialogOpen}
        filename={dialogPath === null ? "" : toCanonical(dialogPath)}
        // Every workspace document opens in managed mode (metadata + markdown
        // download); only the write actions below are gated on `canWrite`.
        showMetadata
        editable={canWrite}
        onSave={handleSave}
        onRechunk={
          canWrite && dialogPath !== null
            ? () => rechunk({ scope, paths: [dialogPath], spec: pipelineSpec })
            : undefined
        }
        rechunking={dialogPath !== null && mutatingPaths.has(dialogPath)}
        onReconvert={
          canWrite && dialogPath !== null && dialogDoc?.has_original
            ? () => handleReconvert([dialogPath])
            : undefined
        }
        onDownloadOriginal={
          dialogPath !== null && dialogDoc?.has_original
            ? () => downloadOriginals([dialogPath])
            : undefined
        }
      />
      <ScopeDialogs ref={dialogs} scope={scope} onBulkDone={clearSelection} />
    </Collapsible>
  );
}
