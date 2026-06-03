import { Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type UploadDocumentOptions, buildAuxLlmConfig, uploadDocument } from "../../lib/api";
import {
  buildCollectionZip,
  buildCollectionZipFromDirectoryInput,
  classifyDropItems,
} from "../../lib/collection-upload";
import { featureFlags } from "../../lib/feature-flags";
import type { PipelineSpec } from "../../lib/types";
import { fileStem, isAbortError } from "../../lib/utils";
import { EMPTY_SCOPE, useDocumentsStore } from "../../stores/documents-store";
import { canWriteGroup, getAllGroups, useSettingsStore } from "../../stores/settings-store";
import { CreateDirectoryDialog } from "../CreateDirectoryDialog";
import { DocumentDialog } from "../DocumentDialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../ui/alert-dialog";
import { Input } from "../ui/input";
import { ErrorBanner } from "./ErrorBanner";
import { PipelineSettingsBar } from "./PipelineSettingsBar";
import { ScopeSection } from "./ScopeSection";
import { UploadArea } from "./UploadArea";

interface ManageDocumentsProps {
  onIncludeDocument?: (filename: string) => void;
  onExcludeDocument?: (filename: string) => void;
}

export function ManageDocuments({ onIncludeDocument, onExcludeDocument }: ManageDocumentsProps) {
  const overrides = useSettingsStore((s) => s.overrides);
  const conversionPipeline = useSettingsStore((s) => s.conversionPipeline);
  const chunkingPipeline = useSettingsStore((s) => s.chunkingPipeline);
  const conversionConfigs = useSettingsStore((s) => s.conversionConfigs);
  const chunkingConfigs = useSettingsStore((s) => s.chunkingConfigs);
  const setConversionPipeline = useSettingsStore((s) => s.setConversionPipeline);
  const setChunkingPipeline = useSettingsStore((s) => s.setChunkingPipeline);
  const assetMode = useSettingsStore((s) => s.assetMode);
  const setAssetMode = useSettingsStore((s) => s.setAssetMode);

  const upload = useDocumentsStore((s) => s.upload);
  const uploadMultiple = useDocumentsStore((s) => s.uploadMultiple);
  const uploadCol = useDocumentsStore((s) => s.uploadCol);
  const createDir = useDocumentsStore((s) => s.createDir);
  const refresh = useDocumentsStore((s) => s.refresh);

  const groups = useMemo(() => getAllGroups(), []);
  const writableGroups = useMemo(() => groups.filter((g) => canWriteGroup(g)), [groups]);

  const [uploadScope, setUploadScope] = useState("");
  const target = useDocumentsStore((s) => s.byScope[uploadScope] ?? EMPTY_SCOPE);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const directoryInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isPreparing, setIsPreparing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [newDocOpen, setNewDocOpen] = useState(false);
  const [showCreateDir, setShowCreateDir] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [pendingOverwrite, setPendingOverwrite] = useState<{
    files: File[];
    conflicting: string[];
  } | null>(null);

  const beginOp = useCallback((): AbortSignal => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    return ctrl.signal;
  }, []);
  const handleCancel = useCallback(() => abortRef.current?.abort(), []);

  const pipelineSpec: PipelineSpec = useMemo(
    () =>
      featureFlags.pipelineSpec
        ? {
            conversion: {
              pipeline: conversionPipeline,
              config: conversionConfigs[conversionPipeline],
            },
            chunking: { pipeline: chunkingPipeline, config: chunkingConfigs[chunkingPipeline] },
            process_assets: assetMode,
          }
        : { process_assets: assetMode },
    [conversionPipeline, chunkingPipeline, conversionConfigs, chunkingConfigs, assetMode],
  );

  const uploadOptions = useMemo<UploadDocumentOptions>(
    () => ({ spec: pipelineSpec, llm: buildAuxLlmConfig(overrides), scope: uploadScope }),
    [pipelineSpec, overrides, uploadScope],
  );

  const uploadInFlight = isPreparing || target.isUploading;
  useEffect(() => {
    if (!uploadInFlight) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [uploadInFlight]);

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const fileArray = Array.from(files);
      setUploadError(null);

      const stems = fileArray.map((f) => fileStem(f.name));
      const seen = new Set<string>();
      for (const stem of stems) {
        if (seen.has(stem)) {
          setUploadError(`Batch contains files with the same stem "${stem}"`);
          return;
        }
        seen.add(stem);
      }

      const existingStems = new Set(target.documents.map((d) => fileStem(d.filename)));
      const conflicting = stems.filter((s) => existingStems.has(s));
      if (conflicting.length > 0) {
        setPendingOverwrite({ files: fileArray, conflicting });
        return;
      }

      const signal = beginOp();
      if (fileArray.length === 1) {
        await upload(uploadScope, fileArray[0], { ...uploadOptions, signal });
      } else {
        await uploadMultiple(uploadScope, fileArray, { ...uploadOptions, signal });
      }
    },
    [beginOp, upload, uploadMultiple, uploadOptions, uploadScope, target.documents],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);
  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const { items, files } = e.dataTransfer;
      if (files.length === 0 && items.length === 0) return;
      const signal = beginOp();
      setIsPreparing(true);
      try {
        const classification = await classifyDropItems(items, files, signal);
        const hasCollection =
          classification.directories.length > 0 || classification.zipFiles.length > 0;
        if (!hasCollection) {
          void handleFiles(files);
          return;
        }
        const collection = await buildCollectionZip(classification, signal);
        setIsPreparing(false);
        await uploadCol(uploadScope, collection, { ...uploadOptions, signal });
      } catch (err) {
        if (!isAbortError(err)) throw err;
      } finally {
        setIsPreparing(false);
      }
    },
    [beginOp, handleFiles, uploadCol, uploadOptions, uploadScope],
  );

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      void handleFiles(e.target.files);
      if (fileInputRef.current) fileInputRef.current.value = "";
    },
    [handleFiles],
  );

  const handleDirectoryInputChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;
      const signal = beginOp();
      setIsPreparing(true);
      try {
        const collection = await buildCollectionZipFromDirectoryInput(files);
        setIsPreparing(false);
        await uploadCol(uploadScope, collection, { ...uploadOptions, signal });
      } catch (err) {
        if (!isAbortError(err)) throw err;
      } finally {
        setIsPreparing(false);
        if (directoryInputRef.current) directoryInputRef.current.value = "";
      }
    },
    [beginOp, uploadCol, uploadOptions, uploadScope],
  );

  const handleZipInputChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;
      const file = files[0];
      if (!file.name.toLowerCase().endsWith(".zip")) return;
      const signal = beginOp();
      try {
        await uploadCol(uploadScope, file, { ...uploadOptions, signal });
      } catch (err) {
        if (!isAbortError(err)) throw err;
      } finally {
        if (zipInputRef.current) zipInputRef.current.value = "";
      }
    },
    [beginOp, uploadCol, uploadOptions, uploadScope],
  );

  const confirmOverwrite = useCallback(async () => {
    if (!pendingOverwrite) return;
    const { files } = pendingOverwrite;
    setPendingOverwrite(null);
    const signal = beginOp();
    const opts = { ...uploadOptions, overwrite: true, signal };
    if (files.length === 1) {
      await upload(uploadScope, files[0], opts);
    } else {
      await uploadMultiple(uploadScope, files, opts);
    }
  }, [beginOp, pendingOverwrite, upload, uploadMultiple, uploadOptions, uploadScope]);

  const handleSaveNew = useCallback(
    async (filename: string, content: string) => {
      const file = new File([content], filename, { type: "text/plain" });
      await uploadDocument(filename, file, { spec: pipelineSpec, scope: uploadScope });
      await refresh(uploadScope);
      setNewDocOpen(false);
    },
    [pipelineSpec, uploadScope, refresh],
  );

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {uploadError && <ErrorBanner message={uploadError} onDismiss={() => setUploadError(null)} />}

      <UploadArea
        isDragging={isDragging}
        isUploading={target.isUploading}
        isPreparing={isPreparing}
        uploadProgress={target.uploadProgress}
        operationStage={target.operationStage}
        fileInputRef={fileInputRef}
        directoryInputRef={directoryInputRef}
        zipInputRef={zipInputRef}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onFileInputChange={handleFileInputChange}
        onDirectoryInputChange={handleDirectoryInputChange}
        onZipInputChange={handleZipInputChange}
        onSelectFiles={() => fileInputRef.current?.click()}
        onSelectDirectory={() => directoryInputRef.current?.click()}
        onSelectZip={() => zipInputRef.current?.click()}
        onNewDocument={() => setNewDocOpen(true)}
        onNewFolder={() => setShowCreateDir(true)}
        onCancel={handleCancel}
      />

      <PipelineSettingsBar
        conversionPipeline={conversionPipeline}
        chunkingPipeline={chunkingPipeline}
        assetMode={assetMode}
        uploadScope={uploadScope}
        writableGroups={writableGroups}
        onUploadScopeChange={setUploadScope}
        onConversionPipelineChange={setConversionPipeline}
        onChunkingPipelineChange={setChunkingPipeline}
        onAssetModeChange={setAssetMode}
      />

      <div className="p-4 pb-2">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search documents..."
            className="pl-9"
          />
        </div>
      </div>

      <div className="space-y-1 px-4 pb-4">
        <ScopeSection
          scope=""
          label="Your Documents"
          canWrite
          defaultOpen
          searchQuery={searchQuery}
          pipelineSpec={pipelineSpec}
          onIncludeDocument={onIncludeDocument}
          onExcludeDocument={onExcludeDocument}
        />
        {groups.map((groupId) => (
          <ScopeSection
            key={groupId}
            scope={groupId}
            label={groupId}
            canWrite={canWriteGroup(groupId)}
            defaultOpen={false}
            searchQuery={searchQuery}
            pipelineSpec={pipelineSpec}
            onIncludeDocument={onIncludeDocument}
            onExcludeDocument={onExcludeDocument}
          />
        ))}
      </div>

      <DocumentDialog
        open={newDocOpen}
        onOpenChange={(open) => !open && setNewDocOpen(false)}
        filename="new-document.md"
        editable
        isNew
        onSave={handleSaveNew}
      />

      <CreateDirectoryDialog
        open={showCreateDir}
        onOpenChange={setShowCreateDir}
        onCreate={(path) => createDir(uploadScope, path)}
      />

      <AlertDialog
        open={pendingOverwrite !== null}
        onOpenChange={(open) => !open && setPendingOverwrite(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Overwrite existing documents?</AlertDialogTitle>
            <AlertDialogDescription>
              The following documents already exist and will be overwritten:{" "}
              {pendingOverwrite?.conflicting.join(", ")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void confirmOverwrite()}>
              Overwrite
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

