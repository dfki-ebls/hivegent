import { Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  PERSONAL_SCOPE,
  buildAuxLlmConfig,
  canonicalPath,
  groupScope,
  splitScopePath,
  writeDocument,
} from "@/lib/api";
import {
  buildCollectionZip,
  buildCollectionZipFromDirectoryInput,
  classifyDropItems,
} from "@/lib/collection-upload";
import { featureFlags } from "@/lib/feature-flags";
import type { PipelineSpec } from "@/lib/types";
import { errorMessage } from "@/lib/utils";
import { useDocumentsStore } from "@/stores/documents-store";
import { canWriteGroup, getAllGroups, useSettingsStore } from "@/stores/settings-store";
import {
  type UploadOptions,
  selectHasPendingUploads,
  useUploadQueue,
} from "@/stores/upload-queue-store";
import { CreateDirectoryDialog } from "@/components/CreateDirectoryDialog";
import { DocumentDialog } from "@/components/DocumentDialog";
import { Input } from "@/components/ui/input";
import { PipelineSettingsBar } from "@/components/documents/PipelineSettingsBar";
import { ScopeSection } from "@/components/documents/ScopeSection";
import { UploadArea } from "@/components/documents/UploadArea";

export function DocumentManager() {
  const overrides = useSettingsStore((s) => s.overrides);
  const conversionPipeline = useSettingsStore((s) => s.conversionPipeline);
  const chunkingPipeline = useSettingsStore((s) => s.chunkingPipeline);
  const conversionConfigs = useSettingsStore((s) => s.conversionConfigs);
  const chunkingConfigs = useSettingsStore((s) => s.chunkingConfigs);
  const setConversionPipeline = useSettingsStore((s) => s.setConversionPipeline);
  const setChunkingPipeline = useSettingsStore((s) => s.setChunkingPipeline);
  const assetMode = useSettingsStore((s) => s.assetMode);
  const setAssetMode = useSettingsStore((s) => s.setAssetMode);

  const createDir = useDocumentsStore((s) => s.createDir);
  const refresh = useDocumentsStore((s) => s.refresh);
  const enqueueFiles = useUploadQueue((s) => s.enqueueFiles);
  const enqueueCollection = useUploadQueue((s) => s.enqueueCollection);
  const reportUpload = useUploadQueue((s) => s.report);
  const hasPendingUploads = useUploadQueue(selectHasPendingUploads);

  const groups = useMemo(() => getAllGroups(), []);

  // Canonical directory every upload/create lands in — a workspace root or a
  // subdir armed by clicking a folder in the tree. Defaults to the personal root.
  const [targetDir, setTargetDir] = useState(PERSONAL_SCOPE);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const directoryInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [newDocOpen, setNewDocOpen] = useState(false);
  const [showCreateDir, setShowCreateDir] = useState(false);

  const pipelineSpec: PipelineSpec = useMemo(() => {
    const spec: PipelineSpec = {};
    if (featureFlags.pipelineSpec) {
      spec.conversion = {
        pipeline: conversionPipeline,
        config: conversionConfigs[conversionPipeline],
      };
      spec.chunking = { pipeline: chunkingPipeline, config: chunkingConfigs[chunkingPipeline] };
    }
    if (featureFlags.assetSpec) {
      spec.process_assets = assetMode;
    }
    return spec;
  }, [conversionPipeline, chunkingPipeline, conversionConfigs, chunkingConfigs, assetMode]);

  const uploadOptions = useMemo<UploadOptions>(
    () => ({ spec: pipelineSpec, llm: buildAuxLlmConfig(overrides) }),
    [pipelineSpec, overrides],
  );

  // Warn before leaving only while bytes are still local: once an upload hands
  // off to a job it is server-side work that survives a reload (the feed
  // re-seeds it).
  useEffect(() => {
    if (!hasPendingUploads) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [hasPendingUploads]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);
  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  // Classify dropped OS entries and enqueue them into `target` (a canonical
  // directory). Shared by the top drop zone (which uses the armed target) and
  // per-folder drops in the tree (which pass their own folder as the target).
  const uploadTo = useCallback(
    async (target: string, items: DataTransferItem[], files: File[]) => {
      if (files.length === 0 && items.length === 0) return;
      try {
        const classification = await classifyDropItems(items, files);
        const hasCollection =
          classification.directories.length > 0 || classification.zipFiles.length > 0;
        if (hasCollection) {
          enqueueCollection(
            target,
            "collection.zip",
            (signal) => buildCollectionZip(classification, signal),
            uploadOptions,
          );
        } else {
          enqueueFiles(target, classification.looseFiles, uploadOptions);
        }
      } catch (err) {
        // The drop fails before any queue item exists, so surface it as its own
        // failed tray row (a collection that fails mid-build already shows as one).
        reportUpload(target, "Dropped items", errorMessage(err));
      }
    },
    [enqueueCollection, enqueueFiles, reportUpload, uploadOptions],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      void uploadTo(targetDir, Array.from(e.dataTransfer.items), Array.from(e.dataTransfer.files));
    },
    [uploadTo, targetDir],
  );

  // Picked files run through the same classifier as drops, so a selected ZIP is
  // extracted into a collection instead of stored as one opaque document. The
  // picker yields no directory entries, so only loose files and archives arrive.
  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const captured = e.target.files ? Array.from(e.target.files) : [];
      e.target.value = "";
      void uploadTo(targetDir, [], captured);
    },
    [uploadTo, targetDir],
  );

  const handleDirectoryInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      // Snapshot the files before resetting the input clears its FileList; the
      // archive then builds inside the queue from the held File objects.
      const captured = e.target.files ? Array.from(e.target.files) : [];
      e.target.value = "";
      if (captured.length > 0) {
        enqueueCollection(
          targetDir,
          "collection.zip",
          () => buildCollectionZipFromDirectoryInput(captured),
          uploadOptions,
        );
      }
    },
    [enqueueCollection, uploadOptions, targetDir],
  );

  // New in-browser markdown is written synchronously (not as a background job)
  // in "create" mode, so a name collision rejects with a 409 that propagates to
  // the dialog (which stays open with the content) instead of silently
  // overwriting the existing document; the entry appears the moment we refresh.
  const handleSaveNew = useCallback(
    async (filename: string, content: string) => {
      // New documents are markdown: the backend always chunks the content as the
      // `<stem>.md` description, so the on-disk name must carry the `.md` suffix
      // or the entry and its chunk count drift apart.
      const markdownName = filename.replace(/\.md$/i, "") + ".md";
      await writeDocument(
        canonicalPath(targetDir, markdownName),
        content,
        pipelineSpec.chunking,
        "create",
      );
      await refresh(splitScopePath(targetDir).scope);
    },
    [targetDir, pipelineSpec, refresh],
  );

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* One scroll container with a header that sticks on tall enough windows:
          the upload area, pipeline bar, and search stay pinned while the
          document tree scrolls. Keeping the header inside the scroll container
          (rather than a separate fixed box) means the wheel scrolls the list
          even while the pointer is over the search box. On short windows the
          header scrolls away with the list to reclaim vertical space. */}
      <div className="top-0 z-10 bg-background tall:sticky">
        <UploadArea
          isDragging={isDragging}
          fileInputRef={fileInputRef}
          directoryInputRef={directoryInputRef}
          target={targetDir}
          onResetTarget={() => setTargetDir(PERSONAL_SCOPE)}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onFileInputChange={handleFileInputChange}
          onDirectoryInputChange={handleDirectoryInputChange}
          onSelectFiles={() => fileInputRef.current?.click()}
          onSelectDirectory={() => directoryInputRef.current?.click()}
          onNewDocument={() => setNewDocOpen(true)}
          onNewFolder={() => setShowCreateDir(true)}
        />

        <PipelineSettingsBar
          conversionPipeline={conversionPipeline}
          chunkingPipeline={chunkingPipeline}
          assetMode={assetMode}
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
      </div>

      <div className="space-y-1 px-4 pb-4">
        <ScopeSection
          scope={PERSONAL_SCOPE}
          label="~ (Personal)"
          canWrite
          defaultOpen
          searchQuery={searchQuery}
          pipelineSpec={pipelineSpec}
          armedTarget={targetDir}
          onArmTarget={setTargetDir}
          onUploadInto={uploadTo}
        />
        {groups.map((groupId) => (
          <ScopeSection
            key={groupId}
            scope={groupScope(groupId)}
            label={groupId}
            canWrite={canWriteGroup(groupId)}
            defaultOpen={false}
            searchQuery={searchQuery}
            pipelineSpec={pipelineSpec}
            armedTarget={targetDir}
            onArmTarget={setTargetDir}
            onUploadInto={uploadTo}
          />
        ))}
      </div>

      <DocumentDialog
        open={newDocOpen}
        onOpenChange={(open) => !open && setNewDocOpen(false)}
        filename="new-document.md"
        editable
        isNew
        target={targetDir}
        onSave={handleSaveNew}
      />

      <CreateDirectoryDialog
        open={showCreateDir}
        onOpenChange={setShowCreateDir}
        target={targetDir}
        onCreate={(name) => {
          const { scope, local } = splitScopePath(canonicalPath(targetDir, name));
          void createDir(scope, local);
        }}
      />
    </div>
  );
}
