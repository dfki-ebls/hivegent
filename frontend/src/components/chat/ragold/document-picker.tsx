import { type QueryObserverResult, useQueries } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import {
  canonicalPath,
  isWorkspacePath,
  type DirectoryTreeResponse,
  collectFilePaths,
  documentQueries,
} from "@/api/documents";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
import { T_OPTIONS } from "@/components/chat/ragold/shared";

/** Most matches a document picker renders, so thousands of documents stay fast to search. */
const PICKER_LIMIT = 50;

/**
 * Every document of the workspaces the user can read, led by the *used* ones
 * the tool outputs of this conversation captured.
 */
function usePickerDocuments(): { paths: string[]; used: Set<string> } {
  const documents = useFetchedDocumentsStore((state) => state.documents);
  const workspaces = useWorkspaces();
  const scopes = workspaces.map((workspace) => workspace.scope);
  const combine = (results: QueryObserverResult<DirectoryTreeResponse>[]) =>
    scopes.flatMap((scope, index) => {
      const tree = results[index]?.data;

      return tree ? collectFilePaths(tree.root).map((path) => canonicalPath(scope, path)) : [];
    });

  const collection = useQueries({
    queries: scopes.map((scope) => documentQueries.tree(scope)),
    combine,
  });

  const used = new Set(
    [...documents.keys()].filter((path) => isWorkspacePath(path, workspaces)).sort(),
  );

  return { paths: [...new Set([...used, ...collection])], used };
}

/** A searchable list of the documents that renders a bounded number of matches. */
export function DocumentPicker({ onPick }: { onPick: (path: string) => void }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const { paths, used } = usePickerDocuments();

  return (
    <Combobox<string>
      items={paths}
      value={null}
      limit={PICKER_LIMIT}
      onValueChange={(path) => path && onPick(path)}
    >
      <ComboboxInput placeholder={t(($) => $.searchDocuments)} />
      <ComboboxContent>
        <ComboboxEmpty>{t(($) => $.noMatches)}</ComboboxEmpty>
        <ComboboxList>
          {(path: string) => (
            <ComboboxItem key={path} value={path}>
              <span className="truncate">{path}</span>
              {used.has(path) && (
                <Badge variant="secondary" className="ml-auto">
                  {t(($) => $.used)}
                </Badge>
              )}
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}
