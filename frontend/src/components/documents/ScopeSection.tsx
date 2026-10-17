import { FolderOpen, Users } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  PERSONAL_SCOPE,
  buildAuxLlmConfig,
  canonicalPath,
  downloadOriginal,
  splitScopePath,
  writeDocument,
} from "@/lib/api";
import type { TreeItemDrag } from "@/lib/dnd";
import type { DocumentActionId } from "@/lib/document-actions";
import type { PipelineSpec } from "@/lib/types";
import { downloadBlob } from "@/lib/download";
import { basename, collectFilePaths, commonParentDir, errorMessage } from "@/lib/utils";
import { useDocumentFilterStore } from "@/stores/document-filter-store";
import { DEFAULT_SCOPE_STATE, useDocumentsStore } from "@/stores/documents-store";
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
import { toast } from "@/components/ui/toast";
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
 * shared upload area (in DocumentManager) deposits into the selected scope and
 * the store refresh flows back here through `byScope[scope]`.
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
  const state = useDocumentsStore((s) => s.byScope[scope] ?? DEFAULT_SCOPE_STATE);
  const refresh = useDocumentsStore((s) => s.refresh);
  const storeRechunk = useDocumentsStore((s) => s.rechunk);
  const storeReconvert = useDocumentsStore((s) => s.reconvert);
  const storeMove = useDocumentsStore((s) => s.move);
  const clearError = useDocumentsStore((s) => s.clearError);
  const overrides = useSettingsStore((s) => s.overrides);
  const included = useDocumentFilterStore((s) => s.included);
  const excluded = useDocumentFilterStore((s) => s.excluded);
  const toggleInclude = useDocumentFilterStore((s) => s.toggleInclude);
  const toggleExclude = useDocumentFilterStore((s) => s.toggleExclude);

  const { documents, directoryTree, mutatingPaths, error, hasFetched } = state;

  const dialogs = useRef<ScopeDialogsHandle>(null);
  const [isOpen, setIsOpen] = useState(defaultOpen);
  // The target outlives a close, so the dialog keeps its content while it animates out.
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogPath, setDialogPath] = useFollowedState<string | null>(scope, null, followDialog);
  const [selectedFiles, setSelectedFiles] = useFollowedState(scope, new Set<string>(), followSet);
  const filtered = useFuzzySearch(documents, searchQuery, "display_name", "filename");

  // Refresh this scope's documents and tree once on mount.
  useEffect(() => {
    void refresh(scope);
  }, [refresh, scope]);

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

    if (directoryTree) return collectFilePaths(directoryTree.root);

    return documents.map((d) => d.filename);
  }, [isSearching, filtered, directoryTree, documents]);

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
    void storeReconvert(scope, paths, pipelineSpec, buildAuxLlmConfig(overrides));

  const handleDownloadOriginal = useCallback(
    async (path: string) => {
      try {
        const blob = await downloadOriginal(toCanonical(path));
        downloadBlob(blob, basename(docsByFilename.get(path)?.original_path ?? "") || "original");
      } catch (err) {
        toast.add({ type: "error", title: errorMessage(err) });
      }
    },
    [docsByFilename, toCanonical],
  );

  const handleSave = useCallback(
    async (filename: string, content: string) => {
      await writeDocument(filename, content, pipelineSpec.chunking);
      await refresh(scope);
    },
    [pipelineSpec, scope, refresh],
  );

  const bulkHandlers: Record<DocumentActionId, () => void> = {
    rechunk: () => {
      const files = [...selectedFiles];
      clearSelection();
      void storeRechunk(scope, files, pipelineSpec);
    },
    reconvert: () => {
      const files = [...selectedReconvertable];
      clearSelection();
      handleReconvert(files);
    },
    download: () =>
      void selectedReconvertable.reduce(
        (chain, p) => chain.then(() => handleDownloadOriginal(p)),
        Promise.resolve(),
      ),
    delete: () => dialogs.current?.bulkDelete([...selectedFiles]),
  };

  // Resolve a dragged row or selection into one store move. The drag originates
  // in `drag.scope` and lands in this section's `scope`, the same workspace for
  // an in-place move, a different one when migrating between the personal and a
  // shared space (the drop only fires for a valid move). The dragged paths keep
  // their structure relative to their common parent, so a single file or
  // directory keeps its name under the destination.
  const handleMoveInto = useCallback(
    (drag: TreeItemDrag, destDir: string) => {
      const into = (suffix: string) => (destDir ? `${destDir}/${suffix}` : suffix);
      const commonParent = commonParentDir(drag.paths);
      const sameScope = drag.scope === scope;
      const moves = drag.paths
        .map((source) => ({ source, destination: into(source.slice(commonParent.length)) }))
        // Within one workspace a same-path entry is a no-op; across workspaces
        // it still re-homes the entry, so keep it.
        .filter(({ source, destination }) => !sameScope || destination !== source);

      if (drag.paths.length > 1) clearSelection();

      if (moves.length > 0) void storeMove(drag.scope, scope, moves);
    },
    [scope, storeMove, clearSelection],
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

  const fileCount = directoryTree?.total_files ?? documents.length;
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
        {error && <ErrorAlert onDismiss={() => clearError(scope)}>{error}</ErrorAlert>}
        {!hasFetched ? (
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
            ? () => storeRechunk(scope, [dialogPath], pipelineSpec)
            : undefined
        }
        onReconvert={
          canWrite && dialogPath !== null && dialogDoc?.has_original
            ? () => handleReconvert([dialogPath])
            : undefined
        }
        onDownloadOriginal={
          dialogPath !== null && dialogDoc?.has_original
            ? () => void handleDownloadOriginal(dialogPath)
            : undefined
        }
      />
      <ScopeDialogs ref={dialogs} scope={scope} onBulkDone={clearSelection} />
    </Collapsible>
  );
}
