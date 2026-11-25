import type { LucideIcon } from "lucide-react";
import { Download, RotateCcw, Scissors, Trash2 } from "lucide-react";

/** Identifiers for document file-operation actions. */
type DocumentActionId = "rechunk" | "reconvert" | "download" | "delete";

/** A document file-operation action definition. */
interface DocumentAction {
  /** Unique key. */
  id: DocumentActionId;
  /** Lucide icon component. */
  icon: LucideIcon;
  /** Button variant for the batch bar. */
  variant: "secondary" | "destructive";
  /** When true, the action only appears when every selected entry is in a writable workspace. */
  requiresWrite: boolean;
}

/**
 * The bulk document actions rendered by the batch selection bar
 * ({@link BulkActionBar}) as icon + text buttons. Single-document actions live
 * in the document dialog and the tree row's inline delete button.
 */
const DOCUMENT_ACTIONS: readonly DocumentAction[] = [
  {
    id: "rechunk",
    icon: Scissors,
    variant: "secondary",
    requiresWrite: true,
  },
  {
    id: "reconvert",
    icon: RotateCcw,
    variant: "secondary",
    requiresWrite: true,
  },
  {
    id: "download",
    icon: Download,
    variant: "secondary",
    requiresWrite: false,
  },
  {
    id: "delete",
    icon: Trash2,
    variant: "destructive",
    requiresWrite: true,
  },
];

export { DOCUMENT_ACTIONS };
export type { DocumentActionId };
