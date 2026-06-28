import { Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  PERSONAL_SCOPE,
  buildAuxLlmConfig,
  canonicalPath,
  groupScope,
  writeDocument,
} from "../../lib/api";
import {
  buildCollectionZip,
  buildCollectionZipFromDirectoryInput,
  classifyDropItems,
} from "../../lib/collection-upload";
import { featureFlags } from "../../lib/feature-flags";
import type { PipelineSpec } from "../../lib/types";
import { errorMessage } from "../../lib/utils";
import { useDocumentsStore } from "../../stores/documents-store";
import { canWriteGroup, getAllGroups, useSettingsStore } from "../../stores/settings-store";
import {
  type UploadOptions,
  selectHasPendingUploads,
  useUploadQueue,
} from "../../stores/upload-queue-store";
import { CreateDirectoryDialog } from "../CreateDirectoryDialog";
import { DocumentDialog } from "../DocumentDialog";
import { Input } from "../ui/input";
import { PipelineSettingsBar } from "./PipelineSettingsBar";
import { ScopeSection } from "./ScopeSection";
import { UploadArea } from "./UploadArea";

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
  const writableGroups = useMemo(() => groups.filter((g) => canWriteGroup(g)), [groups]);

  const [uploadScope, setUploadScope] = useState(PERSONAL_SCOPE);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const directoryInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
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

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const { items, files } = e.dataTransfer;
      if (files.length === 0 && items.length === 0) return;
      try {
        const classification = await classifyDropItems(items, files);
        const hasCollection =
          classification.directories.length > 0 || classification.zipFiles.length > 0;
        if (hasCollection) {
          enqueueCollection(
            uploadScope,
            "collection.zip",
            (signal) => buildCollectionZip(classification, signal),
            uploadOptions,
          );
        } else {
          enqueueFiles(uploadScope, classification.looseFiles, uploadOptions);
        }
      } catch (err) {
        // The drop fails before any queue item exists, so surface it as its own
        // failed tray row (a collection that fails mid-build already shows as one).
        reportUpload(uploadScope, "Dropped items", errorMessage(err));
      }
    },
    [enqueueCollection, enqueueFiles, reportUpload, uploadOptions, uploadScope],
  );

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files) enqueueFiles(uploadScope, Array.from(e.target.files), uploadOptions);
      e.target.value = "";
    },
    [enqueueFiles, uploadOptions, uploadScope],
  );

  const handleDirectoryInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      // Snapshot the files before resetting the input clears its FileList; the
      // archive then builds inside the queue from the held File objects.
      const captured = e.target.files ? Array.from(e.target.files) : [];
      e.target.value = "";
      if (captured.length > 0) {
        enqueueCollection(
          uploadScope,
          "collection.zip",
          () => buildCollectionZipFromDirectoryInput(captured),
          uploadOptions,
        );
      }
    },
    [enqueueCollection, uploadOptions, uploadScope],
  );

  const handleZipInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = "";
      if (file && file.name.toLowerCase().endsWith(".zip")) {
        enqueueCollection(uploadScope, file.name, () => Promise.resolve(file), uploadOptions);
      }
    },
    [enqueueCollection, uploadOptions, uploadScope],
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
        canonicalPath(uploadScope, markdownName),
        content,
        pipelineSpec.chunking,
        "create",
      );
      await refresh(uploadScope);
    },
    [uploadScope, pipelineSpec, refresh],
  );

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <UploadArea
        isDragging={isDragging}
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
          scope={PERSONAL_SCOPE}
          label="Your Documents"
          canWrite
          defaultOpen
          searchQuery={searchQuery}
          pipelineSpec={pipelineSpec}
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
    </div>
  );
}
