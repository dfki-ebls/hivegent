import type JSZip from "jszip";

export type DropClassification = {
  looseFiles: File[];
  directories: FileSystemDirectoryEntry[];
  zipFiles: File[];
};

type CollectedFile = { path: string; file: File };

const COLLECTION_FILENAME = "collection.zip";
const COLLECTION_MIME = "application/zip";

const isZipFile = (file: File): boolean => file.name.toLowerCase().endsWith(".zip");

const classifyFile = (file: File, classification: DropClassification): void => {
  if (isZipFile(file)) classification.zipFiles.push(file);
  else classification.looseFiles.push(file);
};

const readAllEntries = async (
  reader: FileSystemDirectoryReader,
  signal?: AbortSignal,
): Promise<FileSystemEntry[]> => {
  const all: FileSystemEntry[] = [];
  while (true) {
    signal?.throwIfAborted();
    const batch: FileSystemEntry[] = await new Promise((resolve, reject) => {
      reader.readEntries(resolve, reject);
    });
    if (batch.length === 0) break;
    all.push(...batch);
  }
  return all;
};

const readFileFromEntry = (entry: FileSystemFileEntry): Promise<File> =>
  new Promise((resolve, reject) => {
    entry.file(resolve, reject);
  });

const joinPath = (base: string, name: string): string => (base ? `${base}/${name}` : name);

const walkEntry = async (
  entry: FileSystemEntry,
  basePath = "",
  signal?: AbortSignal,
): Promise<CollectedFile[]> => {
  signal?.throwIfAborted();
  if (entry.isFile) {
    const file = await readFileFromEntry(entry as FileSystemFileEntry);
    return [{ path: joinPath(basePath, entry.name), file }];
  }
  if (entry.isDirectory) {
    const dirEntry = entry as FileSystemDirectoryEntry;
    const children = await readAllEntries(dirEntry.createReader(), signal);
    const currentPath = joinPath(basePath, entry.name);
    const nested = await Promise.all(children.map((child) => walkEntry(child, currentPath, signal)));
    return nested.flat();
  }
  return [];
};

const finalizeZip = async (zip: JSZip): Promise<File> => {
  const blob = await zip.generateAsync({ type: "blob" });
  return new File([blob], COLLECTION_FILENAME, { type: COLLECTION_MIME });
};

export const classifyDropItems = async (
  items: DataTransferItemList,
  fallbackFiles: FileList,
  signal?: AbortSignal,
): Promise<DropClassification> => {
  const classification: DropClassification = {
    looseFiles: [],
    directories: [],
    zipFiles: [],
  };

  const entries = Array.from(items)
    .map((item) => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null))
    .filter((entry): entry is FileSystemEntry => entry !== null);

  if (entries.length === 0) {
    for (const file of Array.from(fallbackFiles)) classifyFile(file, classification);
    return classification;
  }

  const fileEntries: FileSystemFileEntry[] = [];
  for (const entry of entries) {
    if (entry.isDirectory) {
      classification.directories.push(entry as FileSystemDirectoryEntry);
    } else if (entry.isFile) {
      fileEntries.push(entry as FileSystemFileEntry);
    }
  }
  signal?.throwIfAborted();
  const files = await Promise.all(fileEntries.map(readFileFromEntry));
  for (const file of files) classifyFile(file, classification);

  return classification;
};

export const buildCollectionZip = async (
  { looseFiles, directories, zipFiles }: DropClassification,
  signal?: AbortSignal,
): Promise<File> => {
  signal?.throwIfAborted();
  const { default: JSZip } = await import("jszip");
  const zip = new JSZip();

  for (const file of looseFiles) zip.file(file.name, file);

  const collectedPerDir = await Promise.all(directories.map((dir) => walkEntry(dir, "", signal)));
  for (const { path, file } of collectedPerDir.flat()) zip.file(path, file);

  signal?.throwIfAborted();
  const archives = await Promise.all(zipFiles.map((archive) => JSZip.loadAsync(archive)));
  await Promise.all(
    archives.flatMap((inner) =>
      Object.values(inner.files)
        .filter((entry) => !entry.dir)
        .map(async (entry) => {
          const blob = await entry.async("blob");
          zip.file(entry.name, blob);
        }),
    ),
  );

  return finalizeZip(zip);
};

export const buildCollectionZipFromDirectoryInput = async (files: FileList): Promise<File> => {
  const { default: JSZip } = await import("jszip");
  const zip = new JSZip();
  for (const file of Array.from(files)) {
    zip.file(file.webkitRelativePath || file.name, file);
  }
  return finalizeZip(zip);
};
