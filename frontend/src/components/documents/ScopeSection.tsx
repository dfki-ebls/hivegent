import { ChevronDown, ChevronRight, FolderOpen, Loader2, Users } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  PERSONAL_SCOPE,
  buildAuxLlmConfig,
  canonicalPath,
  splitScopePath,
  writeDocument,
} from "@/lib/api";
import { DROP_CLASSES, registerTreeRow, type TreeDropState, type TreeItemDrag } from "@/lib/dnd";
import type { PipelineSpec } from "@/lib/types";
import { downloadBlob } from "@/lib/download";
import { basename, cn, collectFilePaths, commonParentDir } from "@/lib/utils";
import { useDocumentFilterStore } from "@/stores/document-filter-store";
import { DEFAULT_SCOPE_STATE, useDocumentsStore } from "@/stores/documents-store";
import { useSettingsStore } from "@/stores/settings-store";
import { DirectoryTreeView } from "@/components/DirectoryTreeView";
import { DocumentDialog } from "@/components/DocumentDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { BulkActionBar } from "@/components/documents/BulkActionBar";
import { DocumentListItem } from "@/components/documents/DocumentListItem";
import { ErrorBanner } from "@/components/documents/ErrorBanner";
import {
  FilterToggleButtons,
  type FilterEntryState,
} from "@/components/documents/FilterToggleButtons";
import { ScopeDialogs, type ScopeDialogsHandle } from "@/components/documents/ScopeDialogs";

/** Edit/view dialog target within a scope (local path). */
interface ScopeDialogState {
  path: string;
  editable: boolean;
}

interface ScopeSectionProps {
  /** Workspace scope: `~` for personal, `@<group>` for a group. */
  scope: string;
  label: string;
  canWrite: boolean;
  /** Whether the section starts expanded (personal) and shows the upload-target hint. */
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
  const state = useDocumentsStore((s) => s.byScope[scope] ?? DEFAULT_SCOPE_STATE);
  const refresh = useDocumentsStore((s) => s.refresh);
  const storeRechunk = useDocumentsStore((s) => s.rechunk);
  const storeReconvert = useDocumentsStore((s) => s.reconvert);
  const storeBulkRechunk = useDocumentsStore((s) => s.bulkRechunk);
  const storeBulkReconvert = useDocumentsStore((s) => s.bulkReconvert);
  const storeMove = useDocumentsStore((s) => s.move);
  const storeMoveDir = useDocumentsStore((s) => s.moveDir);
  const storeBulkMove = useDocumentsStore((s) => s.bulkMove);
  const clearError = useDocumentsStore((s) => s.clearError);
  const overrides = useSettingsStore((s) => s.overrides);
  const included = useDocumentFilterStore((s) => s.included);
  const excluded = useDocumentFilterStore((s) => s.excluded);
  const toggleInclude = useDocumentFilterStore((s) => s.toggleInclude);
  const toggleExclude = useDocumentFilterStore((s) => s.toggleExclude);

  const { documents, directoryTree, mutatingPaths, error, hasFetched } = state;

  const dialogs = useRef<ScopeDialogsHandle>(null);
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const [dialog, setDialog] = useState<ScopeDialogState | null>(null);
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [filtered, setFiltered] = useState(documents);

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
  const isGroup = scope !== PERSONAL_SCOPE;

  const isSearching = searchQuery.trim().length > 0;
  const expanded = isOpen || isSearching;

  // `filtered` is only read while searching (flatList + visibleFilePaths), so
  // there's no need to mirror `documents` into it on the non-searching path.
  useEffect(() => {
    if (!isSearching) return;
    let cancelled = false;
    void import("fuse.js").then(({ default: Fuse }) => {
      if (cancelled) return;
      const fuse = new Fuse(documents, { keys: ["display_name", "filename"], threshold: 0.4 });
      setFiltered(fuse.search(searchQuery).map((r) => r.item));
    });
    return () => {
      cancelled = true;
    };
  }, [documents, searchQuery, isSearching]);

  const docsByFilename = useMemo(() => new Map(documents.map((d) => [d.filename, d])), [documents]);

  const clearSelection = useCallback(() => setSelectedFiles(new Set()), []);
  const toggleFile = useCallback((path: string) => {
    setSelectedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);
  const toggleDirFiles = useCallback((paths: string[]) => {
    setSelectedFiles((prev) => {
      const all = paths.every((p) => prev.has(p));
      const next = new Set(prev);
      for (const p of paths) {
        if (all) next.delete(p);
        else next.add(p);
      }
      return next;
    });
  }, []);

  const visibleFilePaths = useMemo(() => {
    if (isSearching) return filtered.map((d) => d.filename);
    if (directoryTree) return collectFilePaths(directoryTree.root);
    return documents.map((d) => d.filename);
  }, [isSearching, filtered, directoryTree, documents]);

  const { allSelected, someSelected } = useMemo(() => {
    let count = 0;
    for (const p of visibleFilePaths) if (selectedFiles.has(p)) count++;
    return {
      allSelected: visibleFilePaths.length > 0 && count === visibleFilePaths.length,
      someSelected: count > 0,
    };
  }, [visibleFilePaths, selectedFiles]);

  const toggleSelectAll = useCallback(() => {
    if (allSelected) clearSelection();
    else setSelectedFiles(new Set(visibleFilePaths));
  }, [allSelected, visibleFilePaths, clearSelection]);

  const selectedReconvertable = useMemo(
    () => [...selectedFiles].filter((f) => docsByFilename.get(f)?.has_original === true),
    [selectedFiles, docsByFilename],
  );

  const handleReconvert = useCallback(
    (path: string) =>
      void storeReconvert(scope, path, { spec: pipelineSpec, llm: buildAuxLlmConfig(overrides) }),
    [storeReconvert, scope, pipelineSpec, overrides],
  );

  const handleDownloadOriginal = useCallback(
    async (path: string) => {
      try {
        const { downloadOriginal } = await import("@/lib/api");
        const blob = await downloadOriginal(toCanonical(path));
        const filename = docsByFilename.get(path)?.original_path?.split("/").pop() ?? "original";
        downloadBlob(blob, filename);
      } catch {
        // silently ignore download errors
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

  const bulkHandlers = useMemo<Record<string, () => void>>(
    () => ({
      rechunk: () => {
        const files = [...selectedFiles];
        clearSelection();
        void storeBulkRechunk(scope, files, pipelineSpec);
      },
      reconvert: () => {
        const files = [...selectedReconvertable];
        clearSelection();
        void storeBulkReconvert(scope, files, pipelineSpec, buildAuxLlmConfig(overrides));
      },
      download: () =>
        void selectedReconvertable.reduce(
          (chain, p) => chain.then(() => handleDownloadOriginal(p)),
          Promise.resolve(),
        ),
      delete: () => dialogs.current?.bulkDelete([...selectedFiles]),
    }),
    [
      selectedFiles,
      selectedReconvertable,
      clearSelection,
      storeBulkRechunk,
      storeBulkReconvert,
      pipelineSpec,
      overrides,
      handleDownloadOriginal,
      scope,
    ],
  );

  // Resolve a dragged selection into store moves. The drag originates in
  // `drag.scope` and lands in this section's `scope` — the same workspace for an
  // in-place move, a different one when migrating between the personal and a
  // shared space (the drop only fires for a valid move). A single file or
  // directory keeps its name under the destination, while a multi-file drag
  // preserves its structure relative to the selection's common parent — the
  // shape the bulk endpoint expects.
  const handleMoveInto = useCallback(
    (drag: TreeItemDrag, destDir: string) => {
      const into = (suffix: string) => (destDir ? `${destDir}/${suffix}` : suffix);

      if (drag.kind === "directory") {
        void storeMoveDir(drag.scope, drag.paths[0], scope, into(basename(drag.paths[0])));
        return;
      }

      if (drag.paths.length === 1) {
        void storeMove(drag.scope, drag.paths[0], scope, into(basename(drag.paths[0])));
        return;
      }

      const commonParent = commonParentDir(drag.paths);
      const sameScope = drag.scope === scope;
      const moves = drag.paths
        .map((source) => ({ source, destination: into(source.slice(commonParent.length)) }))
        // Within one workspace a same-path entry is a no-op; across workspaces
        // it still re-homes the entry, so keep it.
        .filter(({ source, destination }) => !sameScope || destination !== source);
      clearSelection();
      if (moves.length > 0) void storeBulkMove(drag.scope, scope, moves);
    },
    [scope, storeMove, storeMoveDir, storeBulkMove, clearSelection],
  );

  const handleArm = useCallback(
    (localDir: string) => onArmTarget(canonicalPath(scope, localDir)),
    [onArmTarget, scope],
  );

  // Local dir armed within this scope, or null when the target is elsewhere.
  const armed = splitScopePath(armedTarget);
  const armedHere = armed.scope === scope ? armed.local : null;

  // The section header is the scope-root drop target — a sibling of the tree,
  // never an ancestor of its rows, so root and per-folder targets never nest.
  const headerRef = useRef<HTMLDivElement>(null);
  const [rootDropState, setRootDropState] = useState<TreeDropState>("none");

  useEffect(() => {
    const element = headerRef.current;
    if (!element || !canWrite) return;

    return registerTreeRow({
      element,
      drag: null,
      drop: {
        scope,
        destDir: "",
        onMove: (drag) => handleMoveInto(drag, ""),
        onUpload: (items, files) => onUploadInto(scope, items, files),
      },
      onDropState: setRootDropState,
    });
  }, [scope, canWrite, handleMoveInto, onUploadInto]);

  const treeView = () => {
    if (
      !directoryTree ||
      (directoryTree.total_files === 0 && directoryTree.total_directories === 0)
    ) {
      return <p className="py-2 text-xs text-muted-foreground">No documents in this workspace</p>;
    }
    return (
      <DirectoryTreeView
        entry={directoryTree.root}
        scope={scope}
        mutatingPaths={mutatingPaths}
        onEditFile={(path) => setDialog({ path, editable: canWrite })}
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
        onArm={canWrite ? handleArm : undefined}
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
      return <p className="py-2 text-xs text-muted-foreground">No matching documents</p>;
    }
    return (
      <div className="space-y-2">
        {filtered.map((doc) => {
          const docMutating = mutatingPaths.has(doc.filename);
          return (
            <DocumentListItem
              key={doc.filename}
              doc={doc}
              isMutating={docMutating}
              onEdit={() => setDialog({ path: doc.filename, editable: canWrite })}
              filterState={filterStateOf(toCanonical(doc.filename))}
              onIncludeDocument={() => toggleInclude(toCanonical(doc.filename))}
              onExcludeDocument={() => toggleExclude(toCanonical(doc.filename))}
              onRemove={() => dialogs.current?.deleteFile(doc.filename)}
              selected={canWrite ? selectedFiles.has(doc.filename) : undefined}
              onToggleSelect={canWrite ? () => toggleFile(doc.filename) : undefined}
            />
          );
        })}
      </div>
    );
  };

  const fileCount = directoryTree?.total_files ?? documents.length;
  const showBulkBar = canWrite && selectedFiles.size > 0;

  // Root of this scope is armed when the target points here with no subpath.
  const isRootArmed = armedHere === "";

  // In a writable scope the header label arms the root (and opens the section);
  // otherwise it is a plain collapse trigger. Same button either way.
  const labelButton = (
    <button
      type="button"
      className="min-w-0 flex-1 truncate text-left text-sm font-medium"
      title={canWrite ? "Set as upload target" : undefined}
      onClick={
        canWrite
          ? () => {
              onArmTarget(scope);
              setIsOpen(true);
            }
          : undefined
      }
    >
      {label}
    </button>
  );

  // The document the dialog is open on, resolved once for its metadata badges
  // and the write-action gates below.
  const dialogDoc = dialog ? docsByFilename.get(dialog.path) : undefined;

  return (
    <Collapsible open={expanded} onOpenChange={setIsOpen} className="mb-1">
      <div
        ref={headerRef}
        className={cn(
          "flex items-center gap-2 rounded-md px-1 py-1.5 hover:bg-muted/50 group",
          isRootArmed && "bg-accent",
          DROP_CLASSES[rootDropState],
        )}
      >
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0">
            {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </Button>
        </CollapsibleTrigger>
        {isGroup ? (
          <Users className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <FolderOpen className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        {canWrite ? labelButton : <CollapsibleTrigger asChild>{labelButton}</CollapsibleTrigger>}
        <div className="flex gap-0.5">
          <FilterToggleButtons
            state={filterStateOf(scope)}
            onInclude={() => toggleInclude(scope)}
            onExclude={() => toggleExclude(scope)}
            compact
            revealOnHover
          />
        </div>
        <Badge variant="secondary" className="shrink-0 text-xs">
          {fileCount}
        </Badge>
      </div>

      <CollapsibleContent>
        {error && (
          <div className="my-2">
            <ErrorBanner message={error} onDismiss={() => clearError(scope)} />
          </div>
        )}
        {!hasFetched ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <>
            {showBulkBar ? (
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
                <div className="flex items-center gap-2 px-2 py-1">
                  <Checkbox
                    checked={allSelected ? true : someSelected ? "indeterminate" : false}
                    onCheckedChange={toggleSelectAll}
                  />
                  <span className="text-xs text-muted-foreground">Select all</span>
                </div>
              )
            )}
            {isSearching ? flatList() : treeView()}
          </>
        )}
      </CollapsibleContent>

      <DocumentDialog
        open={dialog !== null}
        onOpenChange={(open) => !open && setDialog(null)}
        filename={dialog ? toCanonical(dialog.path) : ""}
        // Every workspace document opens in managed mode (metadata + markdown
        // download); only the write actions below are gated on `canWrite`.
        showMetadata={dialog !== null}
        editable={dialog?.editable ?? false}
        onSave={handleSave}
        onRechunk={
          dialog && dialog.editable
            ? async () => {
                if (dialog) await storeRechunk(scope, dialog.path, pipelineSpec);
              }
            : undefined
        }
        onReconvert={
          dialog && dialog.editable && dialogDoc?.has_original
            ? () => handleReconvert(dialog.path)
            : undefined
        }
        onDownloadOriginal={
          dialog && dialogDoc?.has_original
            ? () => void handleDownloadOriginal(dialog.path)
            : undefined
        }
      />
      <ScopeDialogs ref={dialogs} scope={scope} onBulkDone={clearSelection} />
    </Collapsible>
  );
}
