import {
  type DirectoryEntry,
  type DirectoryTreeResponse,
  PERSONAL_SCOPE,
  canonicalPath,
} from "@/api/documents";
import type { WorkspaceInfo } from "@/api/settings";
import { basename, below, joinPath, parentDir } from "@/lib/utils";

/**
 * The model behind the one document tree spanning every workspace. Items are
 * keyed by canonical path, so a workspace is the folder named by its scope
 * (`~`, `@<group>`) and its entries live below it, and the invisible root holds
 * the workspaces.
 */

/** Id of the invisible root, the one id that is no canonical path. */
export const TREE_ROOT = "";

/** A workspace as the tree shows it. */
export interface Workspace {
  /** Canonical scope, the prefix the server named, such as `~` for personal or `@<group>` for a group. */
  scope: string;
  kind: WorkspaceInfo["kind"];
  label: string;
  writable: boolean;
}

interface NodeBase {
  /** Canonical path, {@link TREE_ROOT} for the root. */
  id: string;
  name: string;
  children: string[];
}

interface EntryBase extends NodeBase {
  scope: string;
  writable: boolean;
}

/** A file or folder, marked when it may be a case base and when it has a saved similarity configuration. */
interface PathBase extends EntryBase {
  caseBase: boolean;
  caseBaseCandidate: boolean;
}

export type TreeNode =
  | (NodeBase & { kind: "root" })
  | (EntryBase & { kind: "workspace"; loaded: boolean; fileCount: number })
  | (PathBase & { kind: "directory"; fileCount: number })
  | (PathBase & { kind: "file"; entry: DirectoryEntry });

export type EntryNode = Extract<TreeNode, { kind: "directory" | "file" }>;
export type FileNode = Extract<TreeNode, { kind: "file" }>;
type TreeIndex = ReadonlyMap<string, TreeNode>;

const collator = new Intl.Collator();

/** Subdirectories before files, then by name. */
const byKindThenName = (a: DirectoryEntry, b: DirectoryEntry) =>
  Number(a.type === "file") - Number(b.type === "file") || collator.compare(a.name, b.name);

/** Canonical path of the folder holding *path*, {@link TREE_ROOT} for a workspace. */
const folderOf = (path: string) => parentDir(path).slice(0, -1);

/** The children and file count of a folder holding *children*. */
function folder(children: readonly TreeNode[]) {
  return {
    children: children.map((node) => node.id),
    fileCount: children.reduce(
      (sum, node) => sum + (node.kind === "file" ? 1 : node.kind === "root" ? 0 : node.fileCount),
      0,
    ),
  };
}

/** Index every workspace's entries by canonical path, each folder's children sorted. */
export function buildTreeIndex(
  workspaces: readonly (Workspace & { tree: DirectoryTreeResponse | undefined })[],
): TreeIndex {
  const index = new Map<string, TreeNode>();

  const add = (node: TreeNode): TreeNode => {
    index.set(node.id, node);

    return node;
  };

  const childrenOf = (entry: DirectoryEntry, scope: string, writable: boolean): TreeNode[] =>
    (entry.children ?? []).toSorted(byKindThenName).map((child) => {
      const base = {
        id: canonicalPath(scope, child.path),
        name: child.name,
        scope,
        writable,
        caseBase: child.case_base ?? false,
        caseBaseCandidate: child.case_base_candidate ?? false,
      };

      return add(
        child.type === "file"
          ? { ...base, kind: "file", entry: child, children: [] }
          : { ...base, kind: "directory", ...folder(childrenOf(child, scope, writable)) },
      );
    });

  const shown = workspaces.map(({ scope, label, writable, tree }) =>
    add({
      kind: "workspace",
      id: scope,
      name: label,
      scope,
      writable,
      loaded: tree !== undefined,
      ...folder(tree ? childrenOf(tree.root, scope, writable) : []),
    }),
  );

  add({ kind: "root", id: TREE_ROOT, name: "", children: shown.map((node) => node.id) });

  return index;
}

/**
 * The children of every folder that leads to one of the *matches*, which are
 * file ids, so the tree shows only those files and the folders above them.
 */
export function filterChildren(
  index: TreeIndex,
  matches: ReadonlySet<string>,
): ReadonlyMap<string, string[]> {
  const children = new Map<string, string[]>();

  const keep = (id: string): boolean => {
    const node = getNode(index, id);

    if (node.kind === "file") return matches.has(id);

    const kept = node.children.filter(keep);
    children.set(id, kept);

    return kept.length > 0;
  };

  keep(TREE_ROOT);

  return children;
}

/** The node at *id*, which the tree only asks for once it handed out the id. */
export function getNode(index: TreeIndex, id: string): TreeNode {
  const node = index.get(id);

  if (!node) throw new Error(`Unknown tree item ${id}`);

  return node;
}

/** The folder a drop or upload onto *node* lands in: itself, or a file's folder. */
export function dropFolder(node: TreeNode): string {
  return node.kind === "file" ? folderOf(node.id) : node.id;
}

/** Whether *node* is a folder entries can be moved or uploaded into. */
export function isWritableFolder(node: TreeNode): boolean {
  return (node.kind === "workspace" || node.kind === "directory") && node.writable;
}

function isEntry(node: TreeNode): node is EntryNode {
  return node.kind === "file" || node.kind === "directory";
}

/** Whether *node* is a folder that may be a case base, as the backend decides by cbrkit's rule. */
export function isCaseBaseFolder(node: TreeNode): boolean {
  return node.kind === "directory" && node.caseBaseCandidate;
}

/** Whether *node* is a file or directory of a writable workspace. */
export function isWritableEntry(node: TreeNode): node is EntryNode {
  return isEntry(node) && node.writable;
}

/**
 * Where uploads and new entries land with *focused* focused: the focused
 * folder or a file's folder, the personal workspace when that is read-only.
 */
export function uploadTarget(index: TreeIndex, focused: string | null): string {
  const node = focused === null ? undefined : index.get(focused);

  return node && node.kind !== "root" && node.writable ? dropFolder(node) : PERSONAL_SCOPE;
}

/**
 * The entries *ids* stand for, each once: an id below another one is covered
 * by it, and a workspace stands for its top-level entries.
 */
export function entriesOf(index: TreeIndex, ids: readonly string[]): EntryNode[] {
  const selected = new Set(ids);

  const covered = (id: string): boolean => {
    const parent = folderOf(id);

    return parent !== TREE_ROOT && (selected.has(parent) || covered(parent));
  };

  return ids.flatMap((id) => {
    const node = index.get(id);

    if (!node || node.kind === "root" || covered(id)) return [];

    return node.kind === "workspace"
      ? node.children.map((child) => getNode(index, child)).filter(isEntry)
      : [node];
  });
}

/** Every file in or below *nodes*. */
export function filesOf(index: TreeIndex, nodes: readonly TreeNode[]): FileNode[] {
  return nodes.flatMap((node) =>
    node.kind === "file"
      ? [node]
      : filesOf(
          index,
          node.children.map((child) => getNode(index, child)),
        ),
  );
}

/** A move of canonical `paths`, each to its canonical `destination`. */
interface MoveChange {
  paths: string[];
  destination: (path: string) => string;
}

/** Move *entries* into the folder *dest*, each keeping its name, those already there or holding it staying put. */
export function planMoves(entries: readonly EntryNode[], dest: string): MoveChange {
  return {
    paths: entries
      .filter((node) => folderOf(node.id) !== dest && below(dest, node.id) === null)
      .map((node) => node.id),
    destination: (path) => joinPath(dest, basename(path)),
  };
}

/** Rename *node* in place to *name*. */
export function planRename(node: EntryNode, name: string): MoveChange {
  return { paths: [node.id], destination: () => parentDir(node.id) + name };
}
