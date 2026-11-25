import { useMutation } from "@tanstack/react-query";

import { documentMutations } from "@/api/documents";
import type { FileNode } from "@/lib/document-tree";
import { basename } from "@/lib/utils";

const idsOf = (files: readonly FileNode[]) => files.map((file) => file.id);

/**
 * The actions on tree files with the current pipeline settings, shared by the
 * selection bar and the document dialog. Rechunk and reconvert run as jobs in
 * the tray, and a download takes the originals one at a time.
 */
export function useFileActions() {
  const { mutate: rechunk } = useMutation(documentMutations.rechunk());
  const { mutate: reconvert } = useMutation(documentMutations.reconvert());
  const { mutate: download } = useMutation(documentMutations.downloadOriginals());

  return {
    rechunk: (files: readonly FileNode[]) => rechunk({ paths: idsOf(files) }),
    reconvert: (files: readonly FileNode[]) => reconvert({ paths: idsOf(files) }),
    download: (files: readonly FileNode[]) =>
      download(
        files.map((file) => ({
          path: file.id,
          name: basename(file.entry.original_path ?? "") || "original",
        })),
      ),
  };
}
