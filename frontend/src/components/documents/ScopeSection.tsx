import { ChevronDown, ChevronRight, FolderOpen, Loader2, Users } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { PERSONAL_SCOPE, buildAuxLlmConfig, canonicalPath, uploadDocument } from "../../lib/api";
import type { PipelineSpec } from "../../lib/types";
import { downloadBlob } from "../../lib/download";
import { collectFilePaths } from "../../lib/utils";
import { useDocumentFilterStore } from "../../stores/document-filter-store";
import { DEFAULT_SCOPE_STATE, useDocumentsStore } from "../../stores/documents-store";
import { useSettingsStore } from "../../stores/settings-store";
import { DirectoryTreeView } from "../DirectoryTreeView";
import { DocumentDialog } from "../DocumentDialog";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../ui/collapsible";
import { BulkActionBar } from "./BulkActionBar";
import { DocumentListItem } from "./DocumentListItem";
import { ErrorBanner } from "./ErrorBanner";
import { FilterToggleButtons, type FilterEntryState } from "./FilterToggleButtons";
import { ScopeDialogs, type ScopeDialogsHandle } from "./ScopeDialogs";

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
}

/**
 * Self-contained document manager for one workspace scope. Used identically
 * for the personal store and each group, so the two share one code path. The
 * shared upload area (in ManageDocuments) deposits into the selected scope and
 * the store refresh flows back here through `byScope[scope]`.
 */
export function ScopeSection({
  scope,
  label,
  canWrite,
  defaultOpen,
  searchQuery,
  pipelineSpec,
}: ScopeSectionProps) {
  const state = useDocumentsStore((s) => s.byScope[scope] ?? DEFAULT_SCOPE_STATE);
  const refresh = useDocumentsStore((s) => s.refresh);
  const storeRechunk = useDocumentsStore((s) => s.rechunk);
  const storeReconvert = useDocumentsStore((s) => s.reconvert);
  const storeBulkRechunk = useDocumentsStore((s) => s.bulkRechunk);
  const storeBulkReconvert = useDocumentsStore((s) => s.bulkReconvert);
  const clearError = useDocumentsStore((s) => s.clearError);
  const overrides = useSettingsStore((s) => s.overrides);
  const included = useDocumentFilterStore((s) => s.included);
  const excluded = useDocumentFilterStore((s) => s.excluded);
  const toggleInclude = useDocumentFilterStore((s) => s.toggleInclude);
  const toggleExclude = useDocumentFilterStore((s) => s.toggleExclude);

  const {
    documents,
    directoryTree,
    mutatingPaths,
    bulkProgress,
    operationStage,
    error,
    hasFetched,
  } = state;

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
        const { downloadOriginal } = await import("../../lib/api");
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
      const file = new File([content], filename, { type: "text/plain" });
      await uploadDocument(filename, file, { spec: pipelineSpec });
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
      move: () => dialogs.current?.bulkMove([...selectedFiles]),
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
        mutatingPaths={mutatingPaths}
        operationStage={operationStage}
        onEditFile={(path) => setDialog({ path, editable: canWrite })}
        onInclude={(path) => toggleInclude(toCanonical(path))}
        onExclude={(path) => toggleExclude(toCanonical(path))}
        filterState={(path) => filterStateOf(toCanonical(path))}
        onFileAction={
          canWrite
            ? (path, actionId) => {
                switch (actionId) {
                  case "rechunk":
                    void storeRechunk(scope, path, pipelineSpec);
                    break;
                  case "reconvert":
                    handleReconvert(path);
                    break;
                  case "download":
                    void handleDownloadOriginal(path);
                    break;
                  case "move":
                    dialogs.current?.moveFile(path);
                    break;
                  case "delete":
                    dialogs.current?.deleteFile(path);
                    break;
                }
              }
            : undefined
        }
        onCreateSubdir={canWrite ? (path) => dialogs.current?.createSubdir(path) : undefined}
        onDeleteDir={canWrite ? (path) => dialogs.current?.deleteDir(path) : undefined}
        onMoveDir={canWrite ? (path) => dialogs.current?.moveDir(path) : undefined}
        selectedFiles={canWrite ? selectedFiles : undefined}
        onToggleSelectFile={canWrite ? toggleFile : undefined}
        onToggleSelectDir={canWrite ? toggleDirFiles : undefined}
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
              operationStage={docMutating ? operationStage : null}
              onEdit={() => setDialog({ path: doc.filename, editable: canWrite })}
              filterState={filterStateOf(toCanonical(doc.filename))}
              onIncludeDocument={() => toggleInclude(toCanonical(doc.filename))}
              onExcludeDocument={() => toggleExclude(toCanonical(doc.filename))}
              onReconvert={() => handleReconvert(doc.filename)}
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
  const showBulkBar = canWrite && (selectedFiles.size > 0 || bulkProgress !== null);

  return (
    <Collapsible open={expanded} onOpenChange={setIsOpen} className="mb-1">
      <div className="flex items-center gap-2 rounded-md px-1 py-1.5 hover:bg-muted/50 group">
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
        <CollapsibleTrigger asChild>
          <button type="button" className="min-w-0 flex-1 truncate text-left text-sm font-medium">
            {label}
          </button>
        </CollapsibleTrigger>
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
                bulkProgress={bulkProgress}
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
        showMetadata={dialog?.editable ?? false}
        editable={dialog?.editable ?? false}
        onSave={handleSave}
        onRechunk={
          dialog && dialog.editable
            ? async () => {
                if (dialog) await storeRechunk(scope, dialog.path, pipelineSpec);
              }
            : undefined
        }
      />
      <ScopeDialogs ref={dialogs} scope={scope} onBulkDone={clearSelection} />
    </Collapsible>
  );
}
